"""
One-time historical backfill job — 1990-01-01 to present.

Run once after spinning up a new Turso database to populate historical scores.
Subsequent regular jobs (daily/weekly/monthly/quarterly) maintain the data.

Usage:
  cd ingestion && pip install -e .
  ingest-backfill
  # OR: python -m ingestion.jobs.backfill
"""

from __future__ import annotations

import asyncio
import datetime
import sys

from ingestion.db import _make_client, TursoClient
import numpy as np
import pandas as pd
import structlog

from ingestion.config import settings
from ingestion.db import (
    bootstrap,
    get_all_indicators,
    get_observations_df,
    log_update,
    upsert_headline_scores,
    upsert_indicator_scores,
    upsert_subscores,
)
from ingestion.seed_indicators import seed as seed_indicators
from ingestion.sources import bea, bls, eia, fred
from ingestion.transforms.percentile import (
    forward_fill_quarterly_to_monthly,
    score_indicator,
    transform_mom_3mo_ann,
    transform_net_worth_dpi_ratio,
    transform_yoy,
)
from ingestion.transforms.scoring import (
    SUBSCORE_CONFIG,
    compute_all_subscores,
    compute_biggest_movers,
    compute_deltas,
    compute_headline,
    compute_subscore,
    score_to_band,
)
from ingestion.transforms.validate import (
    validate_backtest_recession_drops,
    validate_headline,
    validate_scores,
)

log = structlog.get_logger(__name__)

BACKFILL_START = "1985-01-01"  # fetch a bit before 1990 so YoY transforms are meaningful


async def _score_all_indicators(
    client: TursoClient,
    indicators: list[dict],
) -> pd.DataFrame:
    """
    Score all indicators, returning a long DataFrame:
      [indicator_slug, score_date, raw_value, percentile_rank, score, smoothed_score]
    """
    all_scored: list[pd.DataFrame] = []

    # Build net worth / DPI ratio from supporting series first
    nw_ind = next((i for i in indicators if i["slug"] == "bogz1fl192090005q"), None)
    dspi_ind = next((i for i in indicators if i["slug"] == "dspi"), None)
    nw_ratio_ind = next((i for i in indicators if i["slug"] == "networth_dpi_ratio"), None)

    # Pre-compute net worth ratio
    nw_ratio_series: pd.Series | None = None
    if nw_ind and dspi_ind and nw_ratio_ind:
        nw_obs = await get_observations_df(client, nw_ind["id"])
        dspi_obs = await get_observations_df(client, dspi_ind["id"])
        if nw_obs and dspi_obs:
            nw_s = pd.Series(
                [v for _, v in nw_obs],
                index=pd.to_datetime([d for d, _ in nw_obs]),
            ).astype(float)
            dspi_s = pd.Series(
                [v for _, v in dspi_obs],
                index=pd.to_datetime([d for d, _ in dspi_obs]),
            ).astype(float)
            nw_ratio_series = transform_net_worth_dpi_ratio(nw_s, dspi_s)

    for ind in indicators:
        if not ind["is_scored"]:
            continue

        slug = ind["slug"]
        freq = ind["frequency"]
        scoring_type = ind["scoring_type"]
        higher_is_better = ind["higher_is_better"]
        if higher_is_better is not None:
            higher_is_better = bool(higher_is_better)

        # Net worth ratio: use pre-computed series
        if slug == "networth_dpi_ratio":
            if nw_ratio_series is None:
                log.warning("score.skip", slug=slug, reason="net worth ratio unavailable")
                continue
            observations = [
                (d.strftime("%Y-%m-%d"), float(v) if not pd.isna(v) else None)
                for d, v in nw_ratio_series.items()
            ]
            transform_fn = None
        else:
            obs_raw = await get_observations_df(client, ind["id"])
            if not obs_raw:
                log.warning("score.skip", slug=slug, reason="no observations")
                continue
            observations = obs_raw

            # Assign transform function based on slug
            transform_fn = _get_transform(slug)

        df = score_indicator(
            observations=observations,
            higher_is_better=higher_is_better,
            scoring_type=scoring_type,
            frequency=freq,
            transform_fn=transform_fn,
        )
        if df.empty:
            continue

        # Normalize score_date to month-start or quarter-start
        df["score_date"] = pd.to_datetime(df["score_date"]).dt.to_period(
            "M" if freq in ("monthly", "weekly", "daily") else "Q"
        ).dt.start_time.dt.strftime("%Y-%m-%d")

        df = df.dropna(subset=["smoothed_score"])
        if df.empty:
            continue

        # Store to indicator_scores table
        rows = df[["score_date", "raw_value", "percentile_rank", "score", "smoothed_score"]].to_dict("records")
        await upsert_indicator_scores(client, ind["id"], rows)
        log.info("score.indicator.ok", slug=slug, rows=len(rows))

        df["indicator_slug"] = slug
        all_scored.append(df)

    if not all_scored:
        return pd.DataFrame(columns=["indicator_slug", "score_date", "smoothed_score"])

    combined = pd.concat(all_scored, ignore_index=True)
    return combined[["indicator_slug", "score_date", "smoothed_score"]]


def _get_transform(slug: str):
    """Map indicator slug to its transform function."""
    yoy_slugs = {
        "real_dpi_yoy",
        "real_ahe_yoy",
        "rrsfs_yoy",
        "cpi_yoy",
        "core_cpi_yoy",
        "shelter_cpi_yoy",
        "real_pce_food_svcs_yoy",
    }
    if slug == "payems_3mo_avg":
        return transform_mom_3mo_ann
    if slug == "real_pce_mom_ann":
        return transform_mom_3mo_ann
    if slug in yoy_slugs:
        return transform_yoy
    return None  # raw level used directly


async def _compute_subscores_and_headline(
    client: TursoClient,
    all_scores_df: pd.DataFrame,
) -> None:
    """Aggregate indicator scores into sub-scores and headline for all dates."""
    if all_scores_df.empty:
        log.warning("subscores.skip", reason="no indicator scores available")
        return

    # Get all unique month-start dates
    all_dates = sorted(all_scores_df["score_date"].unique())

    subscore_rows: list[dict] = []
    headline_rows: list[dict] = []

    for score_date in all_dates:
        subscores = compute_all_subscores(all_scores_df, score_date)
        for slug, score in subscores.items():
            if score is not None and not np.isnan(score):
                subscore_rows.append({
                    "slug": slug,
                    "score_date": score_date,
                    "score": round(score, 2),
                })

        headline = compute_headline(subscores)
        if headline is not None and not np.isnan(headline):
            band, color = score_to_band(headline)
            headline_rows.append({
                "score_date": score_date,
                "score": round(headline, 2),
                "band": band,
                "band_color": color,
                "delta_1m": None,
                "delta_3m": None,
                "delta_12m": None,
                "biggest_gains": [],
                "biggest_drops": [],
            })

    # Upsert sub-scores
    if subscore_rows:
        await upsert_subscores(client, subscore_rows)
        log.info("subscores.upserted", rows=len(subscore_rows))

    # Compute headline deltas now that all rows are available
    headline_df = pd.DataFrame(headline_rows)[["score_date", "score"]] if headline_rows else pd.DataFrame()
    subscore_df = pd.DataFrame(subscore_rows) if subscore_rows else pd.DataFrame()

    for row in headline_rows:
        deltas = compute_deltas(headline_df, row["score_date"])
        row.update(deltas)
        if not subscore_df.empty:
            gains, drops = compute_biggest_movers(subscore_df, row["score_date"])
            row["biggest_gains"] = gains
            row["biggest_drops"] = drops

    if headline_rows:
        await upsert_headline_scores(client, headline_rows)
        log.info("headline.upserted", rows=len(headline_rows))

    # Back-test validation
    if headline_rows:
        warnings = validate_backtest_recession_drops(headline_df)
        if warnings:
            log.warning("backtest.validation", warnings=warnings)
        else:
            log.info("backtest.validation.passed")


async def run() -> None:
    started_at = datetime.datetime.utcnow().isoformat() + "Z"
    log.info("backfill.start", backfill_start=BACKFILL_START)

    client = _make_client()

    try:
        # 1. Bootstrap schema and seed indicators
        await bootstrap(client)
        await seed_indicators(client)
        log.info("backfill.bootstrap.ok")

        # 2. Ingest all FRED series
        log.info("backfill.ingest.fred.start")
        fred_rows = await fred.ingest_all(client, observation_start=BACKFILL_START)
        log.info("backfill.ingest.fred.ok", rows=fred_rows)

        # 3. Ingest BLS supplementary series
        log.info("backfill.ingest.bls.start")
        bls_rows = await bls.ingest_supplementary(client, start_year=1990)
        log.info("backfill.ingest.bls.ok", rows=bls_rows)

        # 4. Ingest BEA NIPA (food services PCE)
        log.info("backfill.ingest.bea.start")
        bea_rows = await bea.ingest_t20600(client)
        log.info("backfill.ingest.bea.ok", rows=bea_rows)

        # 5. Ingest EIA gas prices
        log.info("backfill.ingest.eia.start")
        eia_rows = await eia.ingest_gas_prices(client)
        log.info("backfill.ingest.eia.ok", rows=eia_rows)

        # 6. Score all indicators (expanding-window percentile)
        log.info("backfill.score.start")
        indicators = await get_all_indicators(client, scored_only=False)
        all_scores_df = await _score_all_indicators(client, indicators)
        log.info("backfill.score.ok", rows=len(all_scores_df))

        # 7. Compute sub-scores and headline for all dates
        log.info("backfill.aggregate.start")
        await _compute_subscores_and_headline(client, all_scores_df)
        log.info("backfill.aggregate.ok")

        # 8. Log success
        finished_at = datetime.datetime.utcnow().isoformat() + "Z"
        total_rows = fred_rows + bls_rows + bea_rows + eia_rows
        await log_update(
            client,
            job_name="backfill",
            status="success",
            started_at=started_at,
            finished_at=finished_at,
            rows_upserted=total_rows,
        )
        log.info("backfill.complete", total_obs_rows=total_rows)

    except Exception as exc:
        log.exception("backfill.error", error=str(exc))
        await log_update(
            client,
            job_name="backfill",
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
