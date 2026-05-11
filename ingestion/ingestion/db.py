"""
Turso / libSQL database client and helper functions.

libsql-client 0.3.x is async-native — all public functions here are async.
Use `async with get_client() as client:` or call `await client.close()` explicitly.
"""

from __future__ import annotations

import asyncio
import datetime
import json
import os
import pathlib
from typing import Any

import libsql_client
import structlog

from ingestion.config import settings

log = structlog.get_logger(__name__)

SCHEMA_PATH = pathlib.Path(__file__).parent.parent / "schema.sql"


def _make_client() -> libsql_client.Client:
    return libsql_client.create_client(
        url=settings.turso_database_url,
        auth_token=settings.turso_auth_token,
    )


async def bootstrap(client: libsql_client.Client) -> None:
    """Execute schema.sql against the database (idempotent — uses IF NOT EXISTS / OR IGNORE)."""
    sql = SCHEMA_PATH.read_text()
    statements = [s.strip() for s in sql.split(";") if s.strip()]
    for stmt in statements:
        await client.execute(stmt)
    log.info("db.bootstrap completed", schema=str(SCHEMA_PATH))


async def get_source_id(client: libsql_client.Client, slug: str) -> int:
    result = await client.execute(
        libsql_client.Statement("SELECT id FROM sources WHERE slug = ?", [slug])
    )
    if not result.rows:
        raise ValueError(f"Unknown source slug: {slug!r}")
    return result.rows[0][0]


async def get_indicator_id(client: libsql_client.Client, slug: str) -> int | None:
    result = await client.execute(
        libsql_client.Statement("SELECT id FROM indicators WHERE slug = ?", [slug])
    )
    if not result.rows:
        return None
    return result.rows[0][0]


async def upsert_indicator(client: libsql_client.Client, data: dict[str, Any]) -> int:
    """Insert or replace an indicator row; returns its id."""
    source_id = await get_source_id(client, data["source"])
    await client.execute(
        libsql_client.Statement(
            """
            INSERT INTO indicators
                (slug, series_id, source_id, name, subscore, frequency, units,
                 higher_is_better, scoring_type, weight_in_subscore, lcl_class,
                 is_scored, backfill_start, description_md, why_it_matters_md,
                 limitations_md, notes)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(slug) DO UPDATE SET
                series_id           = excluded.series_id,
                source_id           = excluded.source_id,
                name                = excluded.name,
                subscore            = excluded.subscore,
                frequency           = excluded.frequency,
                units               = excluded.units,
                higher_is_better    = excluded.higher_is_better,
                scoring_type        = excluded.scoring_type,
                weight_in_subscore  = excluded.weight_in_subscore,
                lcl_class           = excluded.lcl_class,
                is_scored           = excluded.is_scored,
                backfill_start      = excluded.backfill_start,
                description_md      = excluded.description_md,
                why_it_matters_md   = excluded.why_it_matters_md,
                limitations_md      = excluded.limitations_md,
                notes               = excluded.notes
            """,
            [
                data["slug"],
                data["series_id"],
                source_id,
                data["name"],
                data["subscore"],
                data["frequency"],
                data.get("units", ""),
                data.get("higher_is_better"),
                data.get("scoring_type", "percentile"),
                data.get("weight_in_subscore", 0.0),
                data.get("lcl_class", "coincident"),
                1 if data.get("is_scored", True) else 0,
                data.get("backfill_start", "1990-01-01"),
                data.get("description_md"),
                data.get("why_it_matters_md"),
                data.get("limitations_md"),
                data.get("notes"),
            ],
        )
    )
    ind_id = await get_indicator_id(client, data["slug"])
    return ind_id


async def upsert_observations(
    client: libsql_client.Client,
    indicator_id: int,
    rows: list[tuple[str, float | None]],  # (obs_date, value)
    vintage_date: str | None = None,
) -> int:
    """Batch-upsert raw observations. Returns row count."""
    if not rows:
        return 0
    vdate = vintage_date or datetime.date.today().isoformat()
    stmts = [
        libsql_client.Statement(
            """
            INSERT INTO indicator_observations (indicator_id, obs_date, value, vintage_date)
            VALUES (?,?,?,?)
            ON CONFLICT(indicator_id, obs_date, vintage_date) DO UPDATE SET
                value = excluded.value
            """,
            [indicator_id, obs_date, value, vdate],
        )
        for obs_date, value in rows
    ]
    # libsql-client batch() accepts up to 2000 statements
    chunk_size = 500
    for i in range(0, len(stmts), chunk_size):
        await client.batch(stmts[i : i + chunk_size])
    return len(rows)


async def upsert_indicator_scores(
    client: libsql_client.Client,
    indicator_id: int,
    rows: list[dict],  # {score_date, raw_value, percentile_rank, score, smoothed_score}
) -> int:
    if not rows:
        return 0
    stmts = [
        libsql_client.Statement(
            """
            INSERT INTO indicator_scores
                (indicator_id, score_date, raw_value, percentile_rank, score, smoothed_score)
            VALUES (?,?,?,?,?,?)
            ON CONFLICT(indicator_id, score_date) DO UPDATE SET
                raw_value       = excluded.raw_value,
                percentile_rank = excluded.percentile_rank,
                score           = excluded.score,
                smoothed_score  = excluded.smoothed_score,
                computed_at     = strftime('%Y-%m-%dT%H:%M:%SZ','now')
            """,
            [
                indicator_id,
                r["score_date"],
                r.get("raw_value"),
                r.get("percentile_rank"),
                r.get("score"),
                r.get("smoothed_score"),
            ],
        )
        for r in rows
    ]
    chunk_size = 500
    for i in range(0, len(stmts), chunk_size):
        await client.batch(stmts[i : i + chunk_size])
    return len(rows)


async def upsert_subscores(
    client: libsql_client.Client,
    rows: list[dict],  # {slug, score_date, score}
) -> int:
    if not rows:
        return 0
    stmts = [
        libsql_client.Statement(
            """
            INSERT INTO subscores (slug, score_date, score)
            VALUES (?,?,?)
            ON CONFLICT(slug, score_date) DO UPDATE SET
                score       = excluded.score,
                computed_at = strftime('%Y-%m-%dT%H:%M:%SZ','now')
            """,
            [r["slug"], r["score_date"], r["score"]],
        )
        for r in rows
    ]
    await client.batch(stmts)
    return len(rows)


async def upsert_headline_scores(
    client: libsql_client.Client,
    rows: list[dict],
) -> int:
    if not rows:
        return 0
    stmts = [
        libsql_client.Statement(
            """
            INSERT INTO headline_scores
                (score_date, score, band, band_color, delta_1m, delta_3m, delta_12m,
                 biggest_gains, biggest_drops)
            VALUES (?,?,?,?,?,?,?,?,?)
            ON CONFLICT(score_date) DO UPDATE SET
                score        = excluded.score,
                band         = excluded.band,
                band_color   = excluded.band_color,
                delta_1m     = excluded.delta_1m,
                delta_3m     = excluded.delta_3m,
                delta_12m    = excluded.delta_12m,
                biggest_gains = excluded.biggest_gains,
                biggest_drops = excluded.biggest_drops,
                computed_at  = strftime('%Y-%m-%dT%H:%M:%SZ','now')
            """,
            [
                r["score_date"],
                r["score"],
                r["band"],
                r["band_color"],
                r.get("delta_1m"),
                r.get("delta_3m"),
                r.get("delta_12m"),
                json.dumps(r.get("biggest_gains", [])),
                json.dumps(r.get("biggest_drops", [])),
            ],
        )
        for r in rows
    ]
    chunk_size = 500
    for i in range(0, len(stmts), chunk_size):
        await client.batch(stmts[i : i + chunk_size])
    return len(rows)


async def get_observations_df(
    client: libsql_client.Client,
    indicator_id: int,
    start_date: str = "1990-01-01",
) -> "list[tuple[str, float | None]]":
    """Return [(obs_date, value)] ordered by obs_date, using the latest vintage."""
    result = await client.execute(
        libsql_client.Statement(
            """
            SELECT obs_date, value
            FROM indicator_observations
            WHERE indicator_id = ?
              AND obs_date >= ?
            GROUP BY obs_date
            HAVING vintage_date = MAX(vintage_date)
            ORDER BY obs_date ASC
            """,
            [indicator_id, start_date],
        )
    )
    return [(row[0], row[1]) for row in result.rows]


async def get_all_indicators(
    client: libsql_client.Client,
    scored_only: bool = False,
) -> list[dict]:
    """Return all indicator rows as dicts."""
    where = "WHERE i.is_scored = 1" if scored_only else ""
    result = await client.execute(
        f"""
        SELECT i.id, i.slug, i.series_id, s.slug as source, i.name, i.subscore,
               i.frequency, i.units, i.higher_is_better, i.scoring_type,
               i.weight_in_subscore, i.lcl_class, i.is_scored, i.backfill_start
        FROM indicators i
        JOIN sources s ON s.id = i.source_id
        {where}
        ORDER BY i.subscore, i.id
        """
    )
    cols = [
        "id", "slug", "series_id", "source", "name", "subscore",
        "frequency", "units", "higher_is_better", "scoring_type",
        "weight_in_subscore", "lcl_class", "is_scored", "backfill_start",
    ]
    return [dict(zip(cols, row)) for row in result.rows]


async def log_update(
    client: libsql_client.Client,
    job_name: str,
    status: str,
    started_at: str,
    finished_at: str | None = None,
    rows_upserted: int = 0,
    error_msg: str | None = None,
) -> None:
    await client.execute(
        libsql_client.Statement(
            """
            INSERT INTO updates (job_name, started_at, finished_at, status, rows_upserted, error_msg)
            VALUES (?,?,?,?,?,?)
            """,
            [job_name, started_at, finished_at, status, rows_upserted, error_msg],
        )
    )
