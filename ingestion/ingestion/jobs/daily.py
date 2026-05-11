"""
Daily ingestion job — runs weekdays at 9am ET.

Fetches highest-frequency indicators:
  - TSA checkpoint throughput (published ~9am ET Mon-Fri)
  - SEC EDGAR check for new 8-K filings from watchlist (Phase 3)

Lightweight: only updates a few indicators, no full re-score.
"""

from __future__ import annotations

import asyncio
import datetime

from ingestion.db import _make_client, TursoClient
import structlog

from ingestion.config import settings
from ingestion.db import bootstrap, log_update
from ingestion.seed_indicators import seed as seed_indicators
from ingestion.sources import tsa

log = structlog.get_logger(__name__)


async def run() -> None:
    started_at = datetime.datetime.utcnow().isoformat() + "Z"
    log.info("daily.start")

    client = _make_client()

    try:
        await bootstrap(client)
        await seed_indicators(client)

        # TSA throughput (daily)
        tsa_rows = await tsa.ingest_throughput(client)
        log.info("daily.tsa.ok", rows=tsa_rows)

        # TODO Phase 3: SEC EDGAR check for new earnings releases
        # edgar_filings = await sec_edgar.check_new_filings(client, since_days=1)

        finished_at = datetime.datetime.utcnow().isoformat() + "Z"
        await log_update(
            client,
            job_name="daily",
            status="success",
            started_at=started_at,
            finished_at=finished_at,
            rows_upserted=tsa_rows,
        )
        log.info("daily.complete")

    except Exception as exc:
        log.exception("daily.error", error=str(exc))
        await log_update(
            client,
            job_name="daily",
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
