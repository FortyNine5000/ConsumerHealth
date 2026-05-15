"""
Expanding-window percentile scoring — the mathematical core of the Consumer Compass.

Key design principle (spec §9.1):
  For each time t, the percentile rank of value_t is computed against only
  the historical data available at time t (values[0:t+1]).
  This eliminates look-ahead bias — the score for 1995 only uses 1990–1995 data.

Scoring types:
  - 'percentile': standard expanding-window rank → 0-100
  - 'proximity_2pct': |value - 2.0| distance → 0-100 (for CPI indicators)
  - 'context_only': returns None (Manheim MUVVI — not scored)
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def expanding_percentile_rank(series: pd.Series) -> pd.Series:
    """
    Compute an expanding-window percentile rank for each observation.

    At each index i, computes the percentile of series[i] within series[:i+1],
    using only non-NaN values. Returns values in [0, 100].

    Formula (interpolated rank):
      percentile = (rank - 1) / (n - 1) * 100
    where rank = number of values <= current value (1-based) and n = window size.
    Single-observation windows return 50.0.

    This is O(n²) which is acceptable for ≤2000 observations (weekly series
    since 1990 ≈ 1800 observations; runtime < 1s).
    """
    values = series.to_numpy(dtype=float)
    result = np.full(len(values), np.nan)

    for i in range(len(values)):
        if np.isnan(values[i]):
            continue
        # Extract all non-NaN values up to and including i
        window = values[: i + 1]
        window = window[~np.isnan(window)]
        n = len(window)

        if n == 1:
            result[i] = 50.0
            continue

        # Rank: count of values strictly less than current, plus 1
        rank = np.sum(window < values[i]) + 1
        # Ties: count how many values equal current; use midpoint rank
        ties = np.sum(window == values[i])
        if ties > 1:
            rank = np.sum(window < values[i]) + (ties + 1) / 2.0

        result[i] = (rank - 1) / (n - 1) * 100.0

    return pd.Series(result, index=series.index, name=series.name)


def apply_direction(percentile: pd.Series, higher_is_better: bool) -> pd.Series:
    """
    Flip the percentile if lower values are better.

    higher_is_better=True  → score = percentile (high percentile = healthy)
    higher_is_better=False → score = 100 - percentile (low percentile = healthy)
    """
    if higher_is_better:
        return percentile
    return 100.0 - percentile


def proximity_2pct_score(series: pd.Series, target: float = 2.0) -> pd.Series:
    """
    Score CPI-type indicators by proximity to a target (default 2%).

    Steps:
      1. Compute absolute distance = |value - target|
      2. Apply expanding_percentile_rank to the distance series
      3. Flip (lower distance = better = higher score)

    Result: score=100 when current reading is closest to target in history;
            score=0 when it's farthest.

    Handles both above-target (inflation) and below-target (deflation) symmetrically.
    """
    distances = (series - target).abs()
    # Rename so the distance series doesn't shadow the original
    distances.name = f"{series.name}_distance" if series.name else "distance"
    distance_pct = expanding_percentile_rank(distances)
    # Flip: lower distance rank → higher score
    return 100.0 - distance_pct


def smooth_scores(
    scores: pd.Series,
    frequency: str,
) -> pd.Series:
    """
    Apply smoothing to indicator scores based on data frequency.

    monthly   → 3-period trailing moving average
    quarterly → 1-period (no smoothing; quarterly data is already slow-moving)
    weekly    → 4-week trailing moving average (mirrors IC4WSA 4-week convention)
    daily     → 30-day trailing moving average

    Uses min_periods=1 so early observations are not dropped.
    """
    windows = {
        "monthly": 3,
        "quarterly": 1,
        "weekly": 4,
        "daily": 30,
    }
    w = windows.get(frequency, 3)
    if w <= 1:
        return scores
    return scores.rolling(window=w, min_periods=1).mean()


def score_indicator(
    observations: list[tuple[str, float | None]],
    higher_is_better: bool | None,
    scoring_type: str,
    frequency: str,
    transform_fn: "callable | None" = None,
) -> pd.DataFrame:
    """
    Full scoring pipeline for a single indicator.

    Args:
        observations: [(date_str, value), ...] sorted chronologically
        higher_is_better: True/False; None for context_only
        scoring_type: 'percentile' | 'proximity_2pct' | 'context_only'
        frequency: 'monthly' | 'quarterly' | 'weekly' | 'daily'
        transform_fn: optional callable(pd.Series) -> pd.Series applied to raw values
                      before percentile computation (e.g. compute YoY, 3mo avg, etc.)

    Returns:
        DataFrame with columns:
          score_date, raw_value, percentile_rank, score, smoothed_score
    """
    if not observations:
        return pd.DataFrame(
            columns=["score_date", "raw_value", "percentile_rank", "score", "smoothed_score"]
        )

    dates, values = zip(*observations)
    series = pd.Series(
        [v for v in values],
        index=pd.to_datetime(dates),
        dtype=float,
    )
    series.name = "value"

    # Apply optional transform (YoY, MoM change, 3mo avg, etc.)
    raw_series = transform_fn(series) if transform_fn else series

    if scoring_type == "context_only" or higher_is_better is None and scoring_type != "proximity_2pct":
        # Context-only: return NaN scores
        df = pd.DataFrame({"raw_value": raw_series})
        df["score_date"] = df.index.strftime("%Y-%m-%d")
        df["percentile_rank"] = np.nan
        df["score"] = np.nan
        df["smoothed_score"] = np.nan
        return df[["score_date", "raw_value", "percentile_rank", "score", "smoothed_score"]]

    if scoring_type == "proximity_2pct":
        pct_rank = proximity_2pct_score(raw_series)
        directed = pct_rank  # already flipped in proximity_2pct_score
    else:
        # Standard percentile
        pct_rank = expanding_percentile_rank(raw_series)
        directed = apply_direction(pct_rank, higher_is_better=bool(higher_is_better))

    smoothed = smooth_scores(directed, frequency=frequency)

    df = pd.DataFrame({
        "score_date": raw_series.index.strftime("%Y-%m-%d"),
        "raw_value": raw_series.values,
        "percentile_rank": pct_rank.values,
        "score": directed.values,
        "smoothed_score": smoothed.values,
    })

    return df


# ── Common transform functions ────────────────────────────────────────────────

def transform_yoy(series: pd.Series) -> pd.Series:
    """Year-over-year percent change."""
    return series.pct_change(periods=12) * 100.0


def transform_mom_3mo_ann(series: pd.Series) -> pd.Series:
    """Month-over-month change, 3-month rolling average, annualized (×12)."""
    mom = series.diff(1)
    return mom.rolling(window=3, min_periods=1).mean() * 12.0


def transform_qoq_ann(series: pd.Series) -> pd.Series:
    """Quarter-over-quarter percent change, annualized."""
    return series.pct_change(periods=1) * 100.0 * 4.0


def transform_net_worth_dpi_ratio(
    net_worth: pd.Series,
    nominal_dpi_monthly: pd.Series,
) -> pd.Series:
    """
    Compute Household Net Worth / Annualized Nominal DPI ratio.

    net_worth: quarterly series (BOGZ1FL192090005Q; FRED may provide this in millions)
    nominal_dpi_monthly: monthly series (DSPI, $B SAAR — already annualized)

    Aligns to quarterly frequency by resampling DPI to quarter-end.
    Returns quarterly ratio series.
    """
    # DSPI is already SAAR (annualized), so no ×4 needed
    dpi_quarterly = nominal_dpi_monthly.resample("QS").last().ffill()
    # Align indices
    common_idx = net_worth.index.intersection(dpi_quarterly.index)
    numerator = net_worth.loc[common_idx].astype(float)
    denominator = dpi_quarterly.loc[common_idx].astype(float)
    ratio = numerator / denominator

    # FRED's Z.1 net-worth series can arrive in millions while DSPI is in
    # billions SAAR. A raw ratio in the thousands is the tell; normalize the
    # numerator to billions so the displayed ratio is ~7x, not ~7,000x.
    valid_ratio = ratio.replace([np.inf, -np.inf], np.nan).dropna()
    if not valid_ratio.empty and valid_ratio.median() > 100:
        ratio = (numerator / 1000.0) / denominator

    return ratio


def forward_fill_quarterly_to_monthly(
    quarterly_scores: pd.Series,
    monthly_index: pd.DatetimeIndex,
    max_fill_periods: int = 3,
) -> pd.Series:
    """
    Forward-fill quarterly indicator scores into a monthly date index.

    Used when building monthly sub-scores: quarterly data is carried forward
    up to 3 months (one quarter) so it contributes to monthly aggregations.

    quarterly_scores: DatetimeIndex at quarter-start dates
    monthly_index: target monthly DatetimeIndex
    max_fill_periods: maximum months to carry forward (default 3 = one quarter)
    """
    monthly = quarterly_scores.reindex(monthly_index)
    monthly = monthly.ffill(limit=max_fill_periods)
    return monthly
