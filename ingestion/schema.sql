-- The Consumer Compass — Turso (libSQL/SQLite) Database Schema
-- Run once to bootstrap; all statements use IF NOT EXISTS / OR IGNORE for idempotency.

-- ── sources ──────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS sources (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    slug       TEXT    NOT NULL UNIQUE,   -- 'fred', 'bls', 'bea', etc.
    name       TEXT    NOT NULL,
    base_url   TEXT,
    notes      TEXT,
    created_at TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

-- ── indicators ───────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS indicators (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    slug                TEXT    NOT NULL UNIQUE,
    series_id           TEXT    NOT NULL,         -- upstream ID: 'UNRATE', 'DRCCLACBS', …
    source_id           INTEGER NOT NULL REFERENCES sources(id),
    name                TEXT    NOT NULL,
    subscore            TEXT    NOT NULL,          -- 'labor_income', 'credit_stress', …
    frequency           TEXT    NOT NULL CHECK (frequency IN ('daily','weekly','monthly','quarterly')),
    units               TEXT    NOT NULL DEFAULT '',
    higher_is_better    INTEGER,                   -- 1=true, 0=false, NULL=special/context
    scoring_type        TEXT    NOT NULL DEFAULT 'percentile'
                                 CHECK (scoring_type IN ('percentile','proximity_2pct','context_only')),
    weight_in_subscore  REAL    NOT NULL DEFAULT 0.0,
    lcl_class           TEXT    NOT NULL CHECK (lcl_class IN ('leading','coincident','lagging','derived')),
    is_scored           INTEGER NOT NULL DEFAULT 1,   -- 0 = supporting data only
    backfill_start      TEXT    NOT NULL DEFAULT '1990-01-01',
    description_md      TEXT,
    why_it_matters_md   TEXT,
    limitations_md      TEXT,
    notes               TEXT,
    created_at          TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_indicators_subscore ON indicators(subscore);
CREATE INDEX IF NOT EXISTS idx_indicators_series_id ON indicators(series_id);

-- ── indicator_observations ────────────────────────────────────────────────────
-- Raw values as fetched from upstream APIs.
-- vintage_date = date the value was retrieved (enables revision tracking).
CREATE TABLE IF NOT EXISTS indicator_observations (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    indicator_id  INTEGER NOT NULL REFERENCES indicators(id),
    obs_date      TEXT    NOT NULL,   -- ISO-8601 date (YYYY-MM-DD)
    value         REAL,               -- NULL when upstream reports '.' (missing)
    vintage_date  TEXT    NOT NULL,   -- YYYY-MM-DD when we fetched this
    created_at    TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    UNIQUE (indicator_id, obs_date, vintage_date)
);

CREATE INDEX IF NOT EXISTS idx_obs_indicator_date
    ON indicator_observations(indicator_id, obs_date DESC);
CREATE INDEX IF NOT EXISTS idx_obs_date ON indicator_observations(obs_date);

-- ── indicator_scores ──────────────────────────────────────────────────────────
-- Derived 0-100 percentile scores, one row per indicator per period.
-- raw_value = possibly transformed input (YoY %, 3-month avg, etc.)
-- percentile_rank = 0-100 before directional flip
-- score = final 0-100 (flipped if lower_is_better), pre-smoothing
-- smoothed_score = after 3-month MA / 4-week MA / quarterly (used in subscores)
CREATE TABLE IF NOT EXISTS indicator_scores (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    indicator_id    INTEGER NOT NULL REFERENCES indicators(id),
    score_date      TEXT    NOT NULL,   -- month-start for monthly; quarter-start for quarterly
    raw_value       REAL,
    percentile_rank REAL,               -- 0-100
    score           REAL,               -- 0-100 directed
    smoothed_score  REAL,               -- after smoothing window
    computed_at     TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    UNIQUE (indicator_id, score_date)
);

CREATE INDEX IF NOT EXISTS idx_scores_indicator_date
    ON indicator_scores(indicator_id, score_date DESC);

-- ── subscores ─────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS subscores (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    slug        TEXT    NOT NULL,      -- 'labor_income', 'credit_stress', …
    score_date  TEXT    NOT NULL,
    score       REAL    NOT NULL,      -- equal-weighted mean of smoothed indicator scores
    computed_at TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    UNIQUE (slug, score_date)
);

CREATE INDEX IF NOT EXISTS idx_subscores_slug_date
    ON subscores(slug, score_date DESC);

-- ── headline_scores ───────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS headline_scores (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    score_date    TEXT    NOT NULL UNIQUE,
    score         REAL    NOT NULL,   -- weighted sum of subscore scores (0-100)
    band          TEXT    NOT NULL,   -- 'Healthy', 'Mixed / Watchful', etc.
    band_color    TEXT    NOT NULL,   -- hex color for band
    delta_1m      REAL,               -- change vs prior month
    delta_3m      REAL,               -- change vs 3 months ago
    delta_12m     REAL,               -- change vs 12 months ago
    biggest_gains TEXT,               -- JSON: [{slug, delta}, …]
    biggest_drops TEXT,               -- JSON: [{slug, delta}, …]
    computed_at   TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

-- ── companies ─────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS companies (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker                  TEXT    NOT NULL UNIQUE,
    name                    TEXT    NOT NULL,
    sector                  TEXT    NOT NULL,
    cik                     TEXT,                   -- SEC CIK (zero-padded to 10 digits)
    in_v1_watchlist         INTEGER NOT NULL DEFAULT 0,
    in_expanded_watchlist   INTEGER NOT NULL DEFAULT 0,
    created_at              TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_companies_ticker ON companies(ticker);

-- ── earnings_quotes ───────────────────────────────────────────────────────────
-- Only SEC 8-K Exhibit 99 or company IR text. Cap at 150 words per spec §8.2.
CREATE TABLE IF NOT EXISTS earnings_quotes (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id           INTEGER NOT NULL REFERENCES companies(id),
    fiscal_quarter       TEXT    NOT NULL,   -- '3Q26'
    calendar_quarter     TEXT    NOT NULL,   -- '2026Q3'
    call_date            TEXT,
    speaker_name         TEXT,
    speaker_title        TEXT,
    quote_text           TEXT    NOT NULL,   -- ≤150 words; fair-use excerpt
    category             TEXT    NOT NULL,   -- JSON array of taxonomy tags
    sentiment_score      INTEGER CHECK (sentiment_score BETWEEN -2 AND 2),
    consumer_segment     TEXT    CHECK (consumer_segment IN ('lower','middle','high','all')),
    metric_referenced    TEXT,
    transcript_link      TEXT,
    source               TEXT    NOT NULL
                          CHECK (source IN ('SEC_8K','Company_IR','ManualNote')),
    related_subscore     TEXT,
    agrees_with_dashboard INTEGER,          -- 1=agrees, 0=contradicts, NULL=neutral
    ai_summary           TEXT,              -- 1-sentence Claude summary
    reviewed_by          TEXT,              -- NULL = awaiting review
    reviewed_at          TEXT,
    created_at           TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_quotes_company_id ON earnings_quotes(company_id);
CREATE INDEX IF NOT EXISTS idx_quotes_calendar_quarter ON earnings_quotes(calendar_quarter);
CREATE INDEX IF NOT EXISTS idx_quotes_agrees ON earnings_quotes(agrees_with_dashboard);

-- ── monthly_reports ───────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS monthly_reports (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    publish_date         TEXT    NOT NULL UNIQUE,   -- YYYY-MM-DD
    slug                 TEXT    NOT NULL UNIQUE,    -- 'consumer-health-2026-04'
    headline             TEXT,
    headline_score       REAL,
    summary_md           TEXT,
    ai_draft_md          TEXT,
    newsletter_sent      INTEGER NOT NULL DEFAULT 0,
    created_at           TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

-- ── updates (job audit log) ───────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS updates (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    job_name      TEXT    NOT NULL,   -- 'ingest_fred', 'score_monthly', …
    started_at    TEXT    NOT NULL,
    finished_at   TEXT,
    status        TEXT    NOT NULL CHECK (status IN ('running','success','failure')),
    rows_upserted INTEGER DEFAULT 0,
    error_msg     TEXT,
    created_at    TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

-- ── Seed: data sources ────────────────────────────────────────────────────────
INSERT OR IGNORE INTO sources (slug, name, base_url) VALUES
    ('fred',    'Federal Reserve Bank of St. Louis FRED', 'https://api.stlouisfed.org'),
    ('bls',     'Bureau of Labor Statistics',             'https://api.bls.gov'),
    ('bea',     'Bureau of Economic Analysis',            'https://apps.bea.gov'),
    ('census',  'U.S. Census Bureau',                    'https://api.census.gov'),
    ('eia',     'Energy Information Administration',      'https://api.eia.gov'),
    ('nyfed',   'Federal Reserve Bank of New York',       'https://www.newyorkfed.org'),
    ('tsa',     'Transportation Security Administration', 'https://www.tsa.gov'),
    ('manheim', 'Manheim / Cox Automotive',               'https://publish.manheim.com'),
    ('freddie', 'Freddie Mac PMMS',                      'https://www.freddiemac.com'),
    ('indeed',  'Indeed Hiring Lab',                     'https://github.com/hiring-lab'),
    ('cfpb',    'Consumer Financial Protection Bureau',  'https://www.consumerfinance.gov'),
    ('sec',     'SEC EDGAR',                             'https://data.sec.gov'),
    ('derived', 'Derived / Computed',                    NULL);
