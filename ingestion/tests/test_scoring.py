"""
Unit tests for ingestion/transforms/scoring.py

Tests:
  - Sub-score weights sum to 1.0
  - compute_subscore: equal-weight mean, NaN handling
  - compute_headline: weighted sum, re-normalization when sub-scores missing
  - score_to_band: correct band assignment at boundaries
  - compute_deltas: correct 1m/3m/12m delta computation
"""

import numpy as np
import pandas as pd
import pytest

from ingestion.transforms.scoring import (
    SUBSCORE_CONFIG,
    build_monthly_score_panel,
    compute_biggest_movers,
    compute_deltas,
    compute_headline,
    compute_subscore,
    score_to_band,
)


class TestSubscoreWeights:
    def test_weights_sum_to_one(self):
        total = sum(v["weight"] for v in SUBSCORE_CONFIG.values())
        assert total == pytest.approx(1.0, abs=1e-9)

    def test_seven_subscores_defined(self):
        assert len(SUBSCORE_CONFIG) == 7

    def test_all_subscores_have_indicators(self):
        for slug, config in SUBSCORE_CONFIG.items():
            assert len(config["indicators"]) > 0, f"{slug} has no indicators"


class TestComputeSubscore:
    def test_equal_weight_mean(self):
        scores = {"a": 80.0, "b": 60.0, "c": 40.0}
        result = compute_subscore(scores)
        assert result == pytest.approx(60.0)

    def test_single_indicator(self):
        result = compute_subscore({"a": 75.0})
        assert result == pytest.approx(75.0)

    def test_all_none_returns_none(self):
        result = compute_subscore({"a": None, "b": None})
        assert result is None

    def test_partial_none_ignored(self):
        result = compute_subscore({"a": 80.0, "b": None, "c": 60.0})
        assert result == pytest.approx(70.0)

    def test_nan_treated_like_none(self):
        result = compute_subscore({"a": 80.0, "b": float("nan"), "c": 60.0})
        assert result == pytest.approx(70.0)

    def test_boundary_values(self):
        assert compute_subscore({"a": 0.0, "b": 100.0}) == pytest.approx(50.0)


class TestComputeHeadline:
    def test_all_subscores_equal_gives_that_value(self):
        subscores = {slug: 70.0 for slug in SUBSCORE_CONFIG}
        result = compute_headline(subscores)
        assert result == pytest.approx(70.0)

    def test_missing_subscore_renormalizes(self):
        """When one sub-score is missing, remaining weights re-normalize to 1.0."""
        subscores = {slug: 60.0 for slug in SUBSCORE_CONFIG}
        # Remove one sub-score — result should still be ~60.0 (since all equal)
        slug_to_remove = "sentiment_expectations"  # weight 0.10
        subscores[slug_to_remove] = None
        result = compute_headline(subscores)
        assert result == pytest.approx(60.0, abs=1.0)

    def test_all_none_returns_none(self):
        subscores = {slug: None for slug in SUBSCORE_CONFIG}
        result = compute_headline(subscores)
        assert result is None

    def test_result_in_0_to_100(self):
        subscores = {slug: 45.0 for slug in SUBSCORE_CONFIG}
        result = compute_headline(subscores)
        assert 0 <= result <= 100

    def test_high_scores_produce_high_headline(self):
        subscores = {slug: 90.0 for slug in SUBSCORE_CONFIG}
        result = compute_headline(subscores)
        assert result > 85.0

    def test_low_scores_produce_low_headline(self):
        subscores = {slug: 15.0 for slug in SUBSCORE_CONFIG}
        result = compute_headline(subscores)
        assert result < 20.0


class TestScoreToBand:
    @pytest.mark.parametrize("score,expected_label", [
        (100.0, "Very Strong"),
        (85.0,  "Very Strong"),
        (84.9,  "Healthy"),
        (70.0,  "Healthy"),
        (69.9,  "Mixed / Watchful"),
        (55.0,  "Mixed / Watchful"),
        (54.9,  "Weakening"),
        (40.0,  "Weakening"),
        (39.9,  "Stressed"),
        (25.0,  "Stressed"),
        (24.9,  "Crisis"),
        (0.0,   "Crisis"),
    ])
    def test_band_boundaries(self, score, expected_label):
        label, _ = score_to_band(score)
        assert label == expected_label, f"Score {score} expected '{expected_label}', got '{label}'"

    def test_returns_hex_color(self):
        _, color = score_to_band(75.0)
        assert color.startswith("#")
        assert len(color) == 7


class TestComputeDeltas:
    def _make_headline_df(self, scores: dict[str, float]) -> pd.DataFrame:
        dates = sorted(scores.keys())
        return pd.DataFrame({"score_date": dates, "score": [scores[d] for d in dates]})

    def test_1m_delta_correct(self):
        df = self._make_headline_df({
            "2024-01-01": 60.0,
            "2024-02-01": 65.0,
            "2024-03-01": 62.0,
        })
        deltas = compute_deltas(df, "2024-02-01")
        assert deltas["delta_1m"] == pytest.approx(5.0)

    def test_12m_delta_correct(self):
        scores = {}
        for i in range(14):
            date = pd.Timestamp("2023-01-01") + pd.DateOffset(months=i)
            scores[date.strftime("%Y-%m-%d")] = 50.0 + i
        df = self._make_headline_df(scores)
        deltas = compute_deltas(df, "2024-01-01")
        assert deltas["delta_12m"] == pytest.approx(12.0)

    def test_missing_prior_returns_none(self):
        df = self._make_headline_df({"2024-01-01": 60.0})
        deltas = compute_deltas(df, "2024-01-01")
        assert deltas["delta_1m"] is None

    def test_unknown_date_returns_none(self):
        df = self._make_headline_df({"2024-01-01": 60.0})
        deltas = compute_deltas(df, "2025-01-01")
        assert deltas["delta_1m"] is None


class TestComputeBiggestMovers:
    def _make_subscore_df(self) -> pd.DataFrame:
        data = []
        for slug in SUBSCORE_CONFIG:
            data.append({"slug": slug, "score_date": "2024-01-01", "score": 55.0})
            data.append({"slug": slug, "score_date": "2024-02-01", "score": 55.0})
        # Make one slug a big gainer and one a big dropper in Feb
        data = [r for r in data if not (r["slug"] in ("labor_income", "credit_stress") and r["score_date"] == "2024-02-01")]
        data.append({"slug": "labor_income", "score_date": "2024-02-01", "score": 75.0})   # +20
        data.append({"slug": "credit_stress", "score_date": "2024-02-01", "score": 30.0})  # -25
        return pd.DataFrame(data)

    def test_biggest_gainer_identified(self):
        df = self._make_subscore_df()
        gains, _ = compute_biggest_movers(df, "2024-02-01")
        assert any(g["slug"] == "labor_income" for g in gains)

    def test_biggest_drop_identified(self):
        df = self._make_subscore_df()
        _, drops = compute_biggest_movers(df, "2024-02-01")
        assert any(d["slug"] == "credit_stress" for d in drops)

    def test_no_movers_returns_empty(self):
        df = pd.DataFrame([
            {"slug": "labor_income", "score_date": "2024-01-01", "score": 60.0},
        ])
        gains, drops = compute_biggest_movers(df, "2024-01-01")
        assert gains == []
        assert drops == []


class TestBuildMonthlyScorePanel:
    def test_carries_stale_values_into_partial_current_month(self):
        df = pd.DataFrame([
            {"indicator_slug": "fast_monthly", "score_date": "2026-04-01", "smoothed_score": 50.0, "frequency": "monthly"},
            {"indicator_slug": "fast_monthly", "score_date": "2026-05-01", "smoothed_score": 90.0, "frequency": "monthly"},
            {"indicator_slug": "slow_monthly", "score_date": "2026-04-01", "smoothed_score": 40.0, "frequency": "monthly"},
            {"indicator_slug": "quarterly", "score_date": "2026-04-01", "smoothed_score": 30.0, "frequency": "quarterly"},
        ])

        panel = build_monthly_score_panel(df)
        may = panel[panel["score_date"] == "2026-05-01"]

        assert dict(zip(may["indicator_slug"], may["smoothed_score"])) == {
            "fast_monthly": 90.0,
            "slow_monthly": 40.0,
            "quarterly": 30.0,
        }

    def test_last_duplicate_month_wins(self):
        df = pd.DataFrame([
            {"indicator_slug": "weekly", "score_date": "2026-05-01", "smoothed_score": 20.0, "frequency": "weekly"},
            {"indicator_slug": "weekly", "score_date": "2026-05-01", "smoothed_score": 30.0, "frequency": "weekly"},
        ])

        panel = build_monthly_score_panel(df)

        assert len(panel) == 1
        assert panel.iloc[0]["smoothed_score"] == pytest.approx(30.0)
