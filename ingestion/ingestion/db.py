"""
Turso database client using the HTTP pipeline API.

Replaces libsql-client (WebSocket/Hrana) which is no longer supported by Turso.
Uses Turso's /v2/pipeline REST endpoint via httpx.
"""

from __future__ import annotations

import asyncio
import datetime
import json
import math
import os
import pathlib
from typing import Any, NamedTuple

import httpx
import structlog

from ingestion.config import settings

log = structlog.get_logger(__name__)

SCHEMA_PATH = pathlib.Path(__file__).parent.parent / "schema.sql"


def _turso_url() -> str:
    return settings.turso_database_url.replace("libsql://", "https://")


def _turso_token() -> str:
    return settings.turso_auth_token


def _encode_value(v: Any) -> dict:
    if v is None:
        return {"type": "null"}
    if isinstance(v, bool):
        return {"type": "integer", "value": "1" if v else "0"}
    if isinstance(v, int):
        return {"type": "integer", "value": str(v)}
    if isinstance(v, float):
        if math.isnan(v) or math.isinf(v):
            return {"type": "null"}
        return {"type": "float", "value": v}
    return {"type": "text", "value": str(v)}


class Statement(NamedTuple):
    sql: str
    args: list = []


class ResultSet:
    def __init__(self, rows: list, columns: list):
        self.rows = rows
        self.columns = columns


class TursoClient:
    def __init__(self, url: str, token: str):
        self._url = url.rstrip("/") + "/v2/pipeline"
        self._headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
        self._http = httpx.AsyncClient(timeout=60.0)

    async def execute(self, sql_or_stmt, args: list | None = None) -> ResultSet:
        if isinstance(sql_or_stmt, Statement):
            sql = sql_or_stmt.sql
            args = list(sql_or_stmt.args)
        else:
            sql = sql_or_stmt
            args = args or []

        payload = {
            "requests": [
                {
                    "type": "execute",
                    "stmt": {
                        "sql": sql,
                        "args": [_encode_value(a) for a in args],
                    },
                },
                {"type": "close"},
            ]
        }
        resp = await self._http.post(self._url, headers=self._headers, json=payload)
        if not resp.is_success:
            raise RuntimeError(f"Turso HTTP {resp.status_code}: {resp.text[:2000]}")
        data = resp.json()
        result = data["results"][0]
        if result["type"] == "error":
            raise RuntimeError(f"Turso error: {result['error']}")
        rs = result["response"]["result"]
        cols = [c["name"] for c in rs.get("cols", [])]
        rows = [
            tuple(
                None if cell["type"] == "null" else
                int(cell["value"]) if cell["type"] == "integer" else
                float(cell["value"]) if cell["type"] == "real" else
                cell["value"]
                for cell in row
            )
            for row in rs.get("rows", [])
        ]
        return ResultSet(rows=rows, columns=cols)

    async def batch(self, stmts: list[Statement]) -> None:
        """Execute multiple statements in a single HTTP request."""
        if not stmts:
            return
        requests = [
            {
                "type": "execute",
                "stmt": {
                    "sql": s.sql,
                    "args": [_encode_value(a) for a in (s.args or [])],
                },
            }
            for s in stmts
        ]
        requests.append({"type": "close"})
        payload = {"requests": requests}
        resp = await self._http.post(self._url, headers=self._headers, json=payload)
        if not resp.is_success:
            raise RuntimeError(f"Turso HTTP {resp.status_code}: {resp.text[:2000]}")
        data = resp.json()
        for i, result in enumerate(data["results"][:-1]):
            if result["type"] == "error":
                raise RuntimeError(f"Turso batch error at stmt {i}: {result['error']}")

    async def close(self) -> None:
        await self._http.aclose()


def _make_client() -> TursoClient:
    return TursoClient(url=_turso_url(), token=_turso_token())


def _split_sql(sql: str) -> list[str]:
    """Split SQL into statements, ignoring semicolons inside -- comments."""
    statements = []
    current: list[str] = []
    for line in sql.splitlines():
        stripped = line.strip()
        # Remove inline comment suffix for semicolon detection only
        comment_pos = stripped.find("--")
        sql_part = stripped[:comment_pos] if comment_pos >= 0 else stripped
        current.append(line)
        if sql_part.rstrip().endswith(";"):
            stmt = "\n".join(current).strip().rstrip(";").strip()
            # Drop pure-comment lines from statement
            clean_lines = [l for l in stmt.splitlines()
                           if l.strip() and not l.strip().startswith("--")]
            clean = "\n".join(clean_lines).strip()
            if clean:
                statements.append(clean)
            current = []
    return statements


async def bootstrap(client: TursoClient) -> None:
    """Execute schema.sql against the database (idempotent — uses IF NOT EXISTS / OR IGNORE)."""
    sql = SCHEMA_PATH.read_text()
    statements = _split_sql(sql)
    for stmt in statements:
        await client.execute(stmt)
    log.info("db.bootstrap completed", schema=str(SCHEMA_PATH), statements=len(statements))


async def get_source_id(client: TursoClient, slug: str) -> int:
    result = await client.execute(
        Statement("SELECT id FROM sources WHERE slug = ?", [slug])
    )
    if not result.rows:
        raise ValueError(f"Unknown source slug: {slug!r}")
    return result.rows[0][0]


async def get_indicator_id(client: TursoClient, slug: str) -> int | None:
    result = await client.execute(
        Statement("SELECT id FROM indicators WHERE slug = ?", [slug])
    )
    if not result.rows:
        return None
    return result.rows[0][0]


async def upsert_indicator(client: TursoClient, data: dict[str, Any]) -> int:
    source_id = await get_source_id(client, data["source"])
    await client.execute(
        Statement(
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
                data["slug"], data["series_id"], source_id, data["name"],
                data["subscore"], data["frequency"], data.get("units", ""),
                data.get("higher_is_better"), data.get("scoring_type", "percentile"),
                data.get("weight_in_subscore", 0.0), data.get("lcl_class", "coincident"),
                1 if data.get("is_scored", True) else 0,
                data.get("backfill_start", "1990-01-01"),
                data.get("description_md"), data.get("why_it_matters_md"),
                data.get("limitations_md"), data.get("notes"),
            ],
        )
    )
    return await get_indicator_id(client, data["slug"])


async def upsert_observations(
    client: TursoClient,
    indicator_id: int,
    rows: list[tuple[str, float | None]],
    vintage_date: str | None = None,
) -> int:
    if not rows:
        return 0
    vdate = vintage_date or datetime.date.today().isoformat()
    stmts = [
        Statement(
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
    chunk_size = 500
    for i in range(0, len(stmts), chunk_size):
        await client.batch(stmts[i : i + chunk_size])
    return len(rows)


async def upsert_indicator_scores(
    client: TursoClient,
    indicator_id: int,
    rows: list[dict],
) -> int:
    if not rows:
        return 0
    stmts = [
        Statement(
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
                indicator_id, r["score_date"], r.get("raw_value"),
                r.get("percentile_rank"), r.get("score"), r.get("smoothed_score"),
            ],
        )
        for r in rows
    ]
    chunk_size = 500
    for i in range(0, len(stmts), chunk_size):
        await client.batch(stmts[i : i + chunk_size])
    return len(rows)


async def upsert_subscores(client: TursoClient, rows: list[dict]) -> int:
    if not rows:
        return 0
    stmts = [
        Statement(
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
    chunk_size = 500
    for i in range(0, len(stmts), chunk_size):
        await client.batch(stmts[i : i + chunk_size])
    return len(rows)


async def upsert_headline_scores(client: TursoClient, rows: list[dict]) -> int:
    if not rows:
        return 0
    stmts = [
        Statement(
            """
            INSERT INTO headline_scores
                (score_date, score, band, band_color, delta_1m, delta_3m, delta_12m,
                 biggest_gains, biggest_drops)
            VALUES (?,?,?,?,?,?,?,?,?)
            ON CONFLICT(score_date) DO UPDATE SET
                score         = excluded.score,
                band          = excluded.band,
                band_color    = excluded.band_color,
                delta_1m      = excluded.delta_1m,
                delta_3m      = excluded.delta_3m,
                delta_12m     = excluded.delta_12m,
                biggest_gains = excluded.biggest_gains,
                biggest_drops = excluded.biggest_drops,
                computed_at   = strftime('%Y-%m-%dT%H:%M:%SZ','now')
            """,
            [
                r["score_date"], r["score"], r["band"], r["band_color"],
                r.get("delta_1m"), r.get("delta_3m"), r.get("delta_12m"),
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
    client: TursoClient,
    indicator_id: int,
    start_date: str = "1990-01-01",
) -> list[tuple[str, float | None]]:
    result = await client.execute(
        Statement(
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
    client: TursoClient,
    scored_only: bool = False,
) -> list[dict]:
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
    client: TursoClient,
    job_name: str,
    status: str,
    started_at: str,
    finished_at: str | None = None,
    rows_upserted: int = 0,
    error_msg: str | None = None,
) -> None:
    await client.execute(
        Statement(
            """
            INSERT INTO updates (job_name, started_at, finished_at, status, rows_upserted, error_msg)
            VALUES (?,?,?,?,?,?)
            """,
            [job_name, started_at, finished_at, status, rows_upserted, error_msg],
        )
    )
