"""
Quarterly ingestion job — runs on 15th of Jan/Apr/Jul/Oct at 9am ET.

Fetches slow-moving quarterly data:
  - FRED quarterly series (TDSP, delinquency rates, SLOOS, Fed Z.1 net worth)
  - NY Fed HHDC transition rates (published ~3 months after quarter end)
  - NY Fed SCE missing payment probability

These are the lagging indicators that provide the most definitive credit stress signal.
"""

from __future__ import annotations

import asyncio
import datetime

import libsql_client
import structlog

from ingestion.config import settings
from ingestion.db import bootstrap, get_all_indicators, log_update
from ingestion.jobs.backfill import _compute_subscores_and_headline, _score_all_indicators
from ingestion.seed_indicators import seed as seed_indicators
from ingestion.sources import nyfed_hhdc, nyfed_sce
from ingestion.sources.fred import ingest_all

log = structlog.get_logger(__name__)

QUARTERLY_FRED_SERIES = [
    # Credit Stress (lagging)
    "DRCCLACBS",        # CC Delinquency Rate
    "DRCLACBS",         # Consumer Loan Delinquency Rate
    "CORCCACBS",        # CC Charge-Off Rate
    "DRTSCLCC",         # SLOOS Net Tightening
    "DRCCLOBS",         # Subprime CC Delinquency (supporting)
    # Balance Sheet (lagging)
    "TDSP",             # Household Debt Service Ratio
    "BOGZ1FL192090005Q",# Household Net Worth (Z.1)
    # Big-Ticket
    "TERMCBCCALLNS",    # Credit Card Interest Rate
    "MSPUS",            # Median Home Sales Price
]

RECENT_START = (datetime.date.today() - datetime.timedelta(days=548)).isoformat()  # ~18 months


async def run() -> None:
    started_at = datetime.datetime.utcnow().isoformat() + "Z"
    log.info("quarterly.start")

    client = libsql_client.create_client(
        url=settings.turso_database_url,
        auth_token=settings.turso_auth_token,
    )

    try:
        await bootstrap(client)
        await seed_indicators(client)

        # Quarterly FRED series
        fred_rows = await ingest_all(
            client,
            observation_start=RECENT_START,
            series_ids=QUARTERLY_FRED_SERIES,
        )
        log.info("quarterly.fred.ok", rows=fred_rows)

        # NY Fed HHDC (quarterly XLSX)
        hhdc_rows = await nyfed_hhdc.ingest_transition_rates(client)
        log.info("quarterly.nyfed_hhdc.ok", rows=hhdc_rows)

        # NY Fed SCE (monthly, but run quarterly to avoid excess scraping)
        sce_rows = await nyfed_sce.ingest_missing_payment(client)
        log.info("quarterly.nyfed_sce.ok", rows=sce_rows)

        # Re-score
        indicators = await get_all_indicators(client, scored_only=False)
        all_scores_df = await _score_all_indicators(client, indicators)
        await _compute_subscores_and_headline(client, all_scores_df)

        finished_at = datetime.datetime.utcnow().isoformat() + "Z"
        total = fred_rows + hhdc_rows + sce_rows
        await log_update(
            client,
            job_name="quarterly",
            status="success",
            started_at=started_at,
            finished_at=finished_at,
            rows_upserted=total,
        )
        log.info("quarterly.complete", rows=total)

    except Exception as exc:
        log.exception("quarterly.error", error=str(exc))
        await log_update(
            client,
            job_name="quarterly",
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
