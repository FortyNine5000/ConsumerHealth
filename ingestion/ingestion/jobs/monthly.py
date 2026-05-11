"""
Monthly ingestion job — runs on the 5th of each month at 9am ET.

Fetches FRED monthly + quarterly series, BLS, BEA, recomputes indicator scores
and headline, then triggers a Cloudflare Pages deploy.

Usage:
  ingest-monthly
  # OR: python -m ingestion.jobs.monthly
"""

from __future__ import annotations

import asyncio
import datetime

import httpx
import libsql_client
import structlog

from ingestion.config import settings
from ingestion.db import (
    bootstrap,
    get_all_indicators,
    log_update,
    upsert_headline_scores,
    upsert_indicator_scores,
    upsert_subscores,
)
from ingestion.jobs.backfill import (
    _compute_subscores_and_headline,
    _score_all_indicators,
)
from ingestion.seed_indicators import seed as seed_indicators
from ingestion.sources import bea, bls, fred

log = structlog.get_logger(__name__)

# Only fetch recent data — last 2 years is enough to update expanding percentiles
# (the full history is already in the DB from backfill)
RECENT_START = (datetime.date.today() - datetime.timedelta(days=730)).isoformat()


async def trigger_deploy() -> None:
    """POST to Cloudflare deploy hook to trigger a Pages rebuild."""
    if not settings.cloudflare_deploy_hook_url:
        log.info("deploy.skip", reason="CLOUDFLARE_DEPLOY_HOOK_URL not set")
        return
    async with httpx.AsyncClient(timeout=30.0) as http:
        try:
            resp = await http.post(settings.cloudflare_deploy_hook_url)
            resp.raise_for_status()
            log.info("deploy.triggered", status=resp.status_code)
        except Exception as exc:
            log.warning("deploy.error", error=str(exc))


async def run() -> None:
    started_at = datetime.datetime.utcnow().isoformat() + "Z"
    log.info("monthly.start")

    client = libsql_client.create_client(
        url=settings.turso_database_url,
        auth_token=settings.turso_auth_token,
    )

    try:
        await bootstrap(client)
        await seed_indicators(client)

        # Ingest updated data from primary sources
        fred_rows = await fred.ingest_all(client, observation_start=RECENT_START)
        log.info("monthly.fred.ok", rows=fred_rows)

        bls_rows = await bls.ingest_supplementary(
            client,
            start_year=datetime.date.today().year - 2,
        )
        log.info("monthly.bls.ok", rows=bls_rows)

        bea_rows = await bea.ingest_t20600(client)
        log.info("monthly.bea.ok", rows=bea_rows)

        # Re-score all indicators using full history from DB
        # (expanding percentile requires the complete history to be correct)
        indicators = await get_all_indicators(client, scored_only=False)
        all_scores_df = await _score_all_indicators(client, indicators)

        # Recompute sub-scores and headline
        await _compute_subscores_and_headline(client, all_scores_df)

        # Trigger Cloudflare deploy
        await trigger_deploy()

        finished_at = datetime.datetime.utcnow().isoformat() + "Z"
        await log_update(
            client,
            job_name="monthly",
            status="success",
            started_at=started_at,
            finished_at=finished_at,
            rows_upserted=fred_rows + bls_rows + bea_rows,
        )
        log.info("monthly.complete")

    except Exception as exc:
        log.exception("monthly.error", error=str(exc))
        await log_update(
            client,
            job_name="monthly",
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
