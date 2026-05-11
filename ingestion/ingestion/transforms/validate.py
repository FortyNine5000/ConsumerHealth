"""
Data validation helpers — run before upserting observations and scores.

Returns lists of warning strings; empty list = all checks passed.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def validate_observations(
    series: pd.Series,
    slug: str,
    expected_min: float | None = None,
    expected_max: float | None = None,
) -> list[str]:
    """
    Validate a raw observation series before scoring.

    Checks:
    - Not empty
    - Null fraction < 10%
    - No implausible values (if bounds provided)
    - Chronologically sorted (no duplicate dates)
    """
    warnings: list[str] = []

    if series.empty:
        warnings.append(f"{slug}: empty series — no observations returned")
        return warnings

    null_pct = series.isna().mean()
    if null_pct > 0.10:
        warnings.append(f"{slug}: {null_pct:.1%} null values exceed 10% threshold")

    if expected_min is not None:
        below = (series.dropna() < expected_min).sum()
        if below > 0:
            warnings.append(f"{slug}: {below} values below expected minimum {expected_min}")

    if expected_max is not None:
        above = (series.dropna() > expected_max).sum()
        if above > 0:
            warnings.append(f"{slug}: {above} values above expected maximum {expected_max}")

    if series.index.duplicated().any():
        warnings.append(f"{slug}: duplicate dates in series")

    if not series.index.is_monotonic_increasing:
        warnings.append(f"{slug}: series is not chronologically sorted")

    return warnings


def validate_scores(scores: pd.Series, slug: str) -> list[str]:
    """
    Validate computed 0-100 indicator scores.

    Checks:
    - All non-NaN values in [0, 100]
    - Not entirely NaN
    """
    warnings: list[str] = []
    non_null = scores.dropna()

    if non_null.empty:
        warnings.append(f"{slug}: all scores are NaN")
        return warnings

    out_of_range = ((non_null < 0) | (non_null > 100.001)).sum()
    if out_of_range > 0:
        bad_vals = non_null[(non_null < 0) | (non_null > 100.001)].head(3).tolist()
        warnings.append(
            f"{slug}: {out_of_range} scores outside [0, 100] — examples: {bad_vals}"
        )

    return warnings


def validate_subscore(
    score: float | None,
    slug: str,
    min_indicators_required: int = 2,
    actual_indicator_count: int = 0,
) -> list[str]:
    """Validate a single sub-score value."""
    warnings: list[str] = []

    if score is None or np.isnan(score):
        warnings.append(f"{slug}: sub-score is NaN — all indicators missing for this date")
        return warnings

    if not (0 <= score <= 100.001):
        warnings.append(f"{slug}: sub-score {score:.2f} out of [0, 100]")

    if actual_indicator_count < min_indicators_required:
        warnings.append(
            f"{slug}: only {actual_indicator_count} indicator(s) available "
            f"(minimum {min_indicators_required} recommended)"
        )

    return warnings


def validate_headline(score: float | None) -> list[str]:
    """Validate the headline composite score."""
    warnings: list[str] = []
    if score is None or np.isnan(score):
        warnings.append("headline: score is NaN")
        return warnings
    if not (0 <= score <= 100.001):
        warnings.append(f"headline: score {score:.2f} out of [0, 100]")
    return warnings


def validate_backtest_recession_drops(
    headline_df: pd.DataFrame,
    recessions: list[tuple[str, str, float]] | None = None,
) -> list[str]:
    """
    Validate that headline scores drop sufficiently during known recessions (spec §13).

    recessions: list of (peak_date, trough_date, required_drop) tuples.
    Default checks four NBER recessions.

    Returns list of warning strings for any recession where the drop was insufficient.
    """
    if recessions is None:
        recessions = [
            ("1990-07-01", "1991-03-01", 15.0),   # 1990–91 recession
            ("2001-03-01", "2001-11-01", 15.0),   # 2001 recession
            ("2007-12-01", "2009-06-01", 15.0),   # GFC
            ("2020-02-01", "2020-04-01", 15.0),   # COVID
        ]

    warnings: list[str] = []
    if headline_df.empty:
        return ["backtest: no headline scores to validate"]

    df = headline_df.sort_values("score_date").copy()
    df["score_date"] = pd.to_datetime(df["score_date"])

    for peak_date, trough_date, required_drop in recessions:
        peak_ts = pd.Timestamp(peak_date)
        trough_ts = pd.Timestamp(trough_date)

        # Score at or just before recession peak
        pre_peak = df[df["score_date"] <= peak_ts]
        at_trough = df[
            (df["score_date"] >= peak_ts) &
            (df["score_date"] <= trough_ts)
        ]

        if pre_peak.empty or at_trough.empty:
            warnings.append(f"backtest: insufficient data for recession {peak_date[:7]}")
            continue

        peak_score = float(pre_peak.iloc[-1]["score"])
        trough_score = float(at_trough["score"].min())
        actual_drop = peak_score - trough_score

        if actual_drop < required_drop:
            warnings.append(
                f"backtest: {peak_date[:7]} recession — "
                f"score dropped {actual_drop:.1f} pts (required {required_drop} pts). "
                f"Peak={peak_score:.1f}, Trough={trough_score:.1f}"
            )

    return warnings
