"""
State / momentum / trend signal components for Consumer Compass v2 testing.

These helpers are intentionally side-effect free. They use only information
available up to each score date, so the resulting candidate scores can be used
in walk-forward validation without look-ahead bias.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from ingestion.transforms.percentile import expanding_percentile_rank


SIGNAL_MIXES: dict[str, dict[str, float]] = {
    "stable_v2": {"state": 0.60, "momentum": 0.25, "trend": 0.15},
    "turning_point": {"state": 0.40, "momentum": 0.40, "trend": 0.20},
}


def _clip_score(value: float | int | None) -> float | None:
    if value is None or pd.isna(value):
        return None
    return float(min(100.0, max(0.0, value)))


def _weighted_score(
    values: dict[str, float | None],
    weights: dict[str, float],
) -> float | None:
    """Weighted 0-100 score with available-component weight re-normalization."""
    total_weight = 0.0
    weighted_sum = 0.0

    for key, weight in weights.items():
        value = values.get(key)
        if value is None or pd.isna(value):
            continue
        weighted_sum += float(value) * weight
        total_weight += weight

    if total_weight == 0:
        return None
    return weighted_sum / total_weight


def score_signal_delta(delta: pd.Series) -> pd.Series:
    """
    Convert an improvement/deterioration series into a walk-forward 0-100 score.

    Positive deltas are healthier. Expanding percentile ranks are computed only
    against historical deltas available as of each date.
    """
    scored = expanding_percentile_rank(delta.astype(float))
    scored.name = delta.name
    return scored


def compute_indicator_signal_components(
    score_panel: pd.DataFrame,
    short_periods: int = 3,
    trend_short_window: int = 3,
    trend_long_window: int = 12,
    mixes: dict[str, dict[str, float]] | None = None,
) -> pd.DataFrame:
    """
    Compute state, short momentum, medium trend, and candidate composite scores.

    Expected input columns:
      - indicator_slug
      - score_date
      - smoothed_score
      - optional frequency, source_score_date, months_stale

    Returns one row per indicator/month. `state_score` is the existing smoothed
    score. `momentum_score` ranks the 3-month score change. `trend_score` ranks
    the 3-month average minus 12-month average spread.
    """
    if score_panel.empty:
        return pd.DataFrame(columns=[
            "indicator_slug",
            "score_date",
            "state_score",
            "momentum_score",
            "trend_score",
            "stable_v2_score",
            "turning_point_score",
            "short_delta",
            "trend_delta",
            "frequency",
            "source_score_date",
            "months_stale",
        ])

    mixes = mixes or SIGNAL_MIXES
    required = {"indicator_slug", "score_date", "smoothed_score"}
    missing = required - set(score_panel.columns)
    if missing:
        raise ValueError(f"score_panel missing required columns: {sorted(missing)}")

    df = score_panel.copy()
    df["score_date"] = pd.to_datetime(df["score_date"])
    df["state_score"] = df["smoothed_score"].map(_clip_score)
    df = df.sort_values(["indicator_slug", "score_date"])

    parts: list[pd.DataFrame] = []
    for indicator_slug, group in df.groupby("indicator_slug", sort=False):
        group = group.sort_values("score_date").copy()
        state = group["state_score"].astype(float)
        short_delta = state.diff(short_periods)
        trend_delta = (
            state.rolling(trend_short_window, min_periods=trend_short_window).mean()
            - state.rolling(trend_long_window, min_periods=trend_long_window).mean()
        )

        group["short_delta"] = short_delta
        group["trend_delta"] = trend_delta
        group["momentum_score"] = score_signal_delta(short_delta).map(_clip_score)
        group["trend_score"] = score_signal_delta(trend_delta).map(_clip_score)

        for mix_name, weights in mixes.items():
            group[f"{mix_name}_score"] = group.apply(
                lambda row: _weighted_score(
                    {
                        "state": row["state_score"],
                        "momentum": row["momentum_score"],
                        "trend": row["trend_score"],
                    },
                    weights,
                ),
                axis=1,
            )

        group["indicator_slug"] = indicator_slug
        parts.append(group)

    result = pd.concat(parts, ignore_index=True)

    optional_columns = ["frequency", "source_score_date", "months_stale"]
    for column in optional_columns:
        if column not in result.columns:
            result[column] = None

    output_columns = [
        "indicator_slug",
        "score_date",
        "state_score",
        "momentum_score",
        "trend_score",
        "stable_v2_score",
        "turning_point_score",
        "short_delta",
        "trend_delta",
        "frequency",
        "source_score_date",
        "months_stale",
    ]
    result = result[output_columns].copy()
    result["score_date"] = result["score_date"].dt.strftime("%Y-%m-%d")
    result["source_score_date"] = pd.to_datetime(
        result["source_score_date"], errors="coerce"
    ).dt.strftime("%Y-%m-%d")
    result["source_score_date"] = result["source_score_date"].where(
        result["source_score_date"] != "NaT", None
    )
    return result


def aggregate_subscore_signal_components(
    indicator_components: pd.DataFrame,
    subscore_config: dict[str, dict[str, Any]],
) -> pd.DataFrame:
    """Aggregate indicator-level signal components into sub-score components."""
    if indicator_components.empty:
        return pd.DataFrame(columns=[
            "slug",
            "score_date",
            "state_score",
            "momentum_score",
            "trend_score",
            "stable_v2_score",
            "turning_point_score",
            "weakening_count",
        ])

    rows: list[dict[str, Any]] = []
    for score_date, date_df in indicator_components.groupby("score_date"):
        slug_to_row = {
            str(row["indicator_slug"]): row
            for _, row in date_df.iterrows()
        }
        for subscore_slug, config in subscore_config.items():
            indicators = config["indicators"]
            matched = [slug_to_row[slug] for slug in indicators if slug in slug_to_row]
            if not matched:
                continue
            frame = pd.DataFrame(matched)
            row: dict[str, Any] = {
                "slug": subscore_slug,
                "score_date": score_date,
                "weakening_count": int((frame["momentum_score"] < 40).sum()),
            }
            for column in [
                "state_score",
                "momentum_score",
                "trend_score",
                "stable_v2_score",
                "turning_point_score",
            ]:
                values = pd.to_numeric(frame[column], errors="coerce").dropna()
                row[column] = float(values.mean()) if not values.empty else None
            rows.append(row)

    return pd.DataFrame(rows)


def aggregate_headline_signal_components(
    subscore_components: pd.DataFrame,
    subscore_config: dict[str, dict[str, Any]],
) -> pd.DataFrame:
    """Aggregate sub-score components into headline-level candidate scores."""
    if subscore_components.empty:
        return pd.DataFrame(columns=[
            "score_date",
            "state_score",
            "momentum_score",
            "trend_score",
            "stable_v2_score",
            "turning_point_score",
            "weakening_count",
        ])

    rows: list[dict[str, Any]] = []
    component_columns = [
        "state_score",
        "momentum_score",
        "trend_score",
        "stable_v2_score",
        "turning_point_score",
    ]

    for score_date, date_df in subscore_components.groupby("score_date"):
        by_slug = {str(row["slug"]): row for _, row in date_df.iterrows()}
        row: dict[str, Any] = {
            "score_date": score_date,
            "weakening_count": int(date_df["weakening_count"].sum()),
        }
        for column in component_columns:
            values: dict[str, float | None] = {}
            weights: dict[str, float] = {}
            for subscore_slug, config in subscore_config.items():
                subscore_row = by_slug.get(subscore_slug)
                if subscore_row is None:
                    continue
                values[subscore_slug] = subscore_row.get(column)
                weights[subscore_slug] = float(config["weight"])
            row[column] = _weighted_score(values, weights)
        rows.append(row)

    return pd.DataFrame(rows)


def rank_weakening_indicators(
    indicator_components: pd.DataFrame,
    score_date: str,
    top_n: int = 5,
) -> list[dict[str, Any]]:
    """Return the most deteriorating indicators for a monitor/report date."""
    if indicator_components.empty:
        return []

    date_df = indicator_components[indicator_components["score_date"] == score_date].copy()
    if date_df.empty:
        return []

    date_df["sort_delta"] = pd.to_numeric(date_df["short_delta"], errors="coerce")
    date_df["sort_momentum"] = pd.to_numeric(date_df["momentum_score"], errors="coerce")
    date_df = date_df.sort_values(
        ["sort_delta", "sort_momentum"],
        ascending=[True, True],
        na_position="last",
    )

    results = []
    for _, row in date_df.head(top_n).iterrows():
        results.append({
            "indicator_slug": row["indicator_slug"],
            "state_score": _clip_score(row["state_score"]),
            "momentum_score": _clip_score(row["momentum_score"]),
            "trend_score": _clip_score(row["trend_score"]),
            "short_delta": None if pd.isna(row["short_delta"]) else float(row["short_delta"]),
        })
    return results
