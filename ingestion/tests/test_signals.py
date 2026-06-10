import pandas as pd
import pytest

from ingestion.transforms.signals import (
    aggregate_headline_signal_components,
    aggregate_subscore_signal_components,
    compute_indicator_signal_components,
    rank_weakening_indicators,
)


SUBSCORE_CONFIG = {
    "labor_income": {
        "weight": 0.6,
        "indicators": ["jobs", "claims"],
    },
    "credit_stress": {
        "weight": 0.4,
        "indicators": ["cards"],
    },
}


def test_indicator_components_keep_headline_state_available_before_momentum():
    df = pd.DataFrame([
        {"indicator_slug": "jobs", "score_date": "2024-01-01", "smoothed_score": 60.0},
        {"indicator_slug": "jobs", "score_date": "2024-02-01", "smoothed_score": 62.0},
        {"indicator_slug": "jobs", "score_date": "2024-03-01", "smoothed_score": 64.0},
    ])

    result = compute_indicator_signal_components(df)

    assert result.iloc[0]["state_score"] == pytest.approx(60.0)
    assert result.iloc[0]["stable_v2_score"] == pytest.approx(60.0)
    assert pd.isna(result.iloc[0]["momentum_score"])


def test_indicator_momentum_scores_deterioration_below_improvement():
    rows = []
    values = [60, 65, 70, 75, 72, 68, 62]
    for idx, value in enumerate(values):
        date = pd.Timestamp("2024-01-01") + pd.DateOffset(months=idx)
        rows.append({
            "indicator_slug": "jobs",
            "score_date": date.strftime("%Y-%m-%d"),
            "smoothed_score": float(value),
        })
    df = pd.DataFrame(rows)

    result = compute_indicator_signal_components(df, short_periods=1)

    improving = result[result["score_date"] == "2024-04-01"].iloc[0]
    weakening = result[result["score_date"] == "2024-07-01"].iloc[0]

    assert improving["momentum_score"] > weakening["momentum_score"]
    assert weakening["short_delta"] < 0


def test_subscore_and_headline_components_aggregate_with_weights():
    indicator_components = pd.DataFrame([
        {
            "indicator_slug": "jobs",
            "score_date": "2024-06-01",
            "state_score": 80.0,
            "momentum_score": 70.0,
            "trend_score": 60.0,
            "stable_v2_score": 74.5,
            "turning_point_score": 72.0,
            "short_delta": 5.0,
        },
        {
            "indicator_slug": "claims",
            "score_date": "2024-06-01",
            "state_score": 60.0,
            "momentum_score": 30.0,
            "trend_score": 40.0,
            "stable_v2_score": 51.5,
            "turning_point_score": 44.0,
            "short_delta": -7.0,
        },
        {
            "indicator_slug": "cards",
            "score_date": "2024-06-01",
            "state_score": 50.0,
            "momentum_score": 20.0,
            "trend_score": 30.0,
            "stable_v2_score": 40.5,
            "turning_point_score": 34.0,
            "short_delta": -10.0,
        },
    ])

    subscores = aggregate_subscore_signal_components(indicator_components, SUBSCORE_CONFIG)
    headline = aggregate_headline_signal_components(subscores, SUBSCORE_CONFIG)

    labor = subscores[subscores["slug"] == "labor_income"].iloc[0]
    assert labor["state_score"] == pytest.approx(70.0)
    assert labor["weakening_count"] == 1

    latest = headline.iloc[0]
    assert latest["state_score"] == pytest.approx((70.0 * 0.6) + (50.0 * 0.4))
    assert latest["weakening_count"] == 2


def test_rank_weakening_indicators_uses_short_delta_first():
    indicator_components = pd.DataFrame([
        {"indicator_slug": "a", "score_date": "2024-06-01", "state_score": 50, "momentum_score": 20, "trend_score": 30, "short_delta": -4},
        {"indicator_slug": "b", "score_date": "2024-06-01", "state_score": 60, "momentum_score": 35, "trend_score": 40, "short_delta": -9},
        {"indicator_slug": "c", "score_date": "2024-06-01", "state_score": 70, "momentum_score": 5, "trend_score": 20, "short_delta": -1},
    ])

    ranked = rank_weakening_indicators(indicator_components, "2024-06-01", top_n=2)

    assert [row["indicator_slug"] for row in ranked] == ["b", "a"]
