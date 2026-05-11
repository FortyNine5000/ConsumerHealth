"""
Weekly ingestion job — runs Thursdays at 10am ET.

Fetches high-frequency FRED series that update weekly:
  - IC4WSA (initial claims, released Thursday)
  - CCSA (continued claims, released Thursday)
  - MORTGAGE30US (Freddie Mac PMMS, released Thursday)
  - EIA gas prices (updated Monday, fetched Thursday)

Recomputes scores and sub-scores for affected indicators.
"""

from __future__ import annotations

import asyncio
import datetime

import httpx
from ingestion.db import _make_client, TursoClient
import structlog

from ingestion.config import settings
from ingestion.db import bootstrap, get_all_indicators, log_update
from ingestion.jobs.backfill import _compute_subscores_and_headline, _score_all_indicators
from ingestion.seed_indicators import seed as seed_indicators
from ingestion.sources import eia
from ingestion.sources.fred import FREDClient, ingest_all

log = structlog.get_logger(__name__)

WEEKLY_FRED_SERIES = [
    "IC4WSA",        # Initial Claims 4-week avg
    "CCSA",          # Continued Claims
    "MORTGAGE30US",  # 30Y Fixed Mortgage Rate
    "T10Y3M",        # Yield Curve (supporting)
    "STLFSI4",       # Financial Stress Index (supporting)
]

RECENT_START = (datetime.date.today() - datetime.timedelta(days=90)).isoformat()


async def run() -> None:
    started_at = datetime.datetime.utcnow().isoformat() + "Z"
    log.info("weekly.start")

    client = _make_client()

    try:
        await bootstrap(client)
        await seed_indicators(client)

        # Fetch weekly FRED series
        fred_rows = await ingest_all(
            client,
            observation_start=RECENT_START,
            series_ids=WEEKLY_FRED_SERIES,
        )
        log.info("weekly.fred.ok", rows=fred_rows)

        # Fetch EIA gas prices (published Mondays)
        eia_rows = await eia.ingest_gas_prices(client)
        log.info("weekly.eia.ok", rows=eia_rows)

        # Re-score affected indicators
        indicators = await get_all_indicators(client, scored_only=False)
        all_scores_df = await _score_all_indicators(client, indicators)
        await _compute_subscores_and_headline(client, all_scores_df)

        finished_at = datetime.datetime.utcnow().isoformat() + "Z"
        await log_update(
            client,
            job_name="weekly",
            status="success",
            started_at=started_at,
            finished_at=finished_at,
            rows_upserted=fred_rows + eia_rows,
        )
        log.info("weekly.complete")

    except Exception as exc:
        log.exception("weekly.error", error=str(exc))
        await log_update(
            client,
            job_name="weekly",
            status="failure",
            started_at=started_at,
            finished_at=datetime.datetime.utcnow().isoformat() + "Z",
            error_msg=str(exc),
        )
        raise
    finally:
        await client.close()


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
