# The Consumer Compass

**Where the data meets the narrative.**

A free, public dashboard tracking U.S. consumer financial health with a 0–100 headline score, 7 sub-scores, 30 underlying indicators, and an earnings-call quote tracker that surfaces the gap between what the data says and what corporations are saying.

## What It Does

- **Headline Consumer Health Score (0–100)** — a weighted composite of 7 sub-scores
- **7 Sub-Scores** — Labor & Income, Household Balance Sheet, Credit Stress, Spending & Demand, Sentiment & Expectations, Inflation & Affordability, Big-Ticket Affordability
- **30 Indicators** — sourced from FRED, BLS, BEA, NY Fed, Census, EIA, Freddie Mac, TSA, and more — all free, citable, no paid vendor dependency
- **Earnings-Call Quote Tracker** — catalogs what banks, card networks, retailers, restaurants, and auto companies say about the consumer, tagged as supporting or contradicting the underlying data

## Score Bands

| Score | Label | Interpretation |
|-------|-------|----------------|
| 85–100 | Very Strong | Broad-based strength across labor, balance sheet, spending, sentiment |
| 70–84 | Healthy | Solid fundamentals; modest pockets of weakness |
| 55–69 | Mixed / Watchful | Divergence across sub-scores; warning signs accumulating |
| 40–54 | Weakening | Multiple sub-scores below trend; pre-recessionary pattern |
| 25–39 | Stressed | Recession-consistent levels in credit and labor |
| 0–24 | Crisis | GFC/COVID-style breakdown |

## Tech Stack

| Layer | Choice |
|-------|--------|
| Site | Astro 5 + Cloudflare Pages |
| Ingestion | Python 3.11 + GitHub Actions |
| Database | Turso (libSQL/SQLite) |
| Charts | Apache ECharts |
| AI | Claude Sonnet (Anthropic API) |
| Newsletter | Beehiiv |

## Repository Structure

```
the-consumer-compass/
├── apps/web/                    # Astro site
│   ├── astro.config.mjs
│   ├── package.json
│   └── src/
│       ├── layouts/
│       ├── pages/
│       ├── components/
│       └── lib/
├── ingestion/                   # Python ingestion pipeline
│   ├── pyproject.toml
│   ├── schema.sql
│   └── ingestion/
│       ├── config.py
│       ├── db.py
│       ├── seed_indicators.py
│       ├── sources/             # Data source connectors
│       ├── transforms/          # Scoring math
│       ├── ai/                  # Claude API integrations
│       └── jobs/                # Scheduled job runners
├── .github/workflows/           # GitHub Actions schedules
├── .env.example
└── docs/
```

## Setup

### 1. Register API Keys

All data sources are free:
- **FRED**: https://fred.stlouisfed.org/docs/api/api_key.html
- **BLS**: https://www.bls.gov/developers/api_signature_v2.htm
- **BEA**: https://apps.bea.gov/api/signup/
- **Census**: https://api.census.gov/data/key_signup.html
- **EIA**: https://www.eia.gov/opendata/register.php
- **Turso**: https://turso.tech (free tier: 100 DBs / 5 GB)

### 2. Configure Environment

```bash
cp .env.example .env
# Fill in your API keys
```

### 3. Install and Run Ingestion

```bash
cd ingestion
pip install -e ".[dev]"
ingest-backfill   # one-time historical backfill 1990–present
ingest-monthly    # regular monthly run
```

### 4. Run Tests

```bash
cd ingestion
pytest tests/
```

### 5. Run the Web App

```bash
cd apps/web
npm install
npm run dev
```

## Data Sources

| Source | What We Pull | Series |
|--------|-------------|--------|
| FRED (St. Louis Fed) | Most indicators | UNRATE, PAYEMS, IC4WSA, CCSA, CES0500000013, PSAVERT, DSPIC96, TDSP, BOGZ1FL192090005Q, DRCCLACBS, DRCLACBS, CORCCACBS, DRTSCLCC, PCEC96, RRSFS, UMCSENT, CSCICP03USM665S, CPIAUCSL, CPILFESL, CUSR0000SAH1, MORTGAGE30US, RIFLPBCIANM72NM, TERMCBCCALLNS, + supporting |
| BLS | Labor market detail | LNS*, CES*, JTS* |
| BEA | PCE food services, personal income | NIPA T20600 |
| EIA | Retail gasoline prices | PET.EMM_EPMR_PTE_NUS_DPG.W |
| NY Fed HHDC | Credit delinquency transitions | Quarterly XLSX |
| NY Fed SCE | Probability of missing payment | Monthly |
| TSA | Travel demand vs. 2019 | Daily scrape |
| Freddie Mac | Mortgage rates | Via FRED MORTGAGE30US |

## Scoring Methodology

The headline score is a weighted sum of 7 sub-scores:

1. Each indicator is transformed to a **0–100 percentile score** based on its expanding historical window (no look-ahead bias)
2. CPI indicators use a special "closer to 2% = better" scoring
3. Sub-scores are **equal-weighted averages** of their indicators
4. Headline score is a **weighted sum** of sub-scores (Labor 20%, Credit 20%, Balance Sheet 15%, Spending 15%, Sentiment 10%, Inflation 10%, Big-Ticket 10%)

See `/methodology` on the live site for full details including back-test validation against 1990, 2001, 2008, and 2020 recessions.

## Legal Notes

- **FRED**: Required citation per UMich license for `UMCSENT`: *"Surveys of Consumers, University of Michigan © [UMCSENT], retrieved from FRED."*
- **Conference Board CCI**: Redistribution prohibited. We use FRED proxy `CSCICP03USM665S` for visualization; headline number cited with link-out.
- **Earnings transcripts**: Only SEC EDGAR Exhibit 99 (public domain) or company IR pages used. Third-party transcripts (Seeking Alpha, Motley Fool) never stored or republished. Quotes capped at 150 words.

## License

MIT License. See [LICENSE](./LICENSE).
