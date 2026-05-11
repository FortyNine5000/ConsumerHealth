"""
Unit tests for ingestion/transforms/percentile.py

Critical correctness requirements:
  - No look-ahead bias: score at t uses only values[0:t+1]
  - Monotone input → monotone output
  - Single observation → 50.0
  - NaN values are skipped but don't corrupt results
  - Flip (lower_is_better) satisfies score + flipped = 100 at every point
  - CPI proximity_2pct: value at target 2% gets highest score
  - Smoothing does not drop early observations (min_periods=1)
"""

import numpy as np
import pandas as pd
import pytest

from ingestion.transforms.percentile import (
    apply_direction,
    expanding_percentile_rank,
    forward_fill_quarterly_to_monthly,
    proximity_2pct_score,
    score_indicator,
    smooth_scores,
    transform_mom_3mo_ann,
    transform_yoy,
)


# ── expanding_percentile_rank ─────────────────────────────────────────────────

class TestExpandingPercentileRank:
    def test_monotone_ascending_produces_increasing_ranks(self, monotone_series):
        result = expanding_percentile_rank(monotone_series)
        non_nan = result.dropna()
        assert non_nan.is_monotonic_increasing, "Ascending input should produce increasing ranks"

    def test_single_observation_returns_50(self):
        s = pd.Series([42.0], index=pd.date_range("2020-01-01", periods=1))
        result = expanding_percentile_rank(s)
        assert result.iloc[0] == pytest.approx(50.0)

    def test_two_observations_extremes(self):
        s = pd.Series([1.0, 2.0], index=pd.date_range("2020-01-01", periods=2))
        result = expanding_percentile_rank(s)
        # First observation: only one value → 50
        assert result.iloc[0] == pytest.approx(50.0)
        # Second observation: 2.0 is max of [1, 2] → rank 2/2 → (2-1)/(2-1)*100 = 100
        assert result.iloc[1] == pytest.approx(100.0)

    def test_no_lookahead_bias(self):
        """Score at t=0 must be based only on [values[0]], not the full series."""
        s = pd.Series(
            [1.0, 100.0, 100.0, 100.0, 100.0],
            index=pd.date_range("2020-01-01", periods=5, freq="MS"),
        )
        result = expanding_percentile_rank(s)
        # At t=0, only one value exists → must be 50.0 regardless of future
        assert result.iloc[0] == pytest.approx(50.0)

    def test_nan_values_skipped(self, series_with_nans):
        result = expanding_percentile_rank(series_with_nans)
        # NaN inputs → NaN outputs (not 0 or some other value)
        assert np.isnan(result.iloc[1])
        assert np.isnan(result.iloc[3])
        # Non-NaN values still computed correctly
        assert not np.isnan(result.iloc[0])
        assert not np.isnan(result.iloc[2])
        assert not np.isnan(result.iloc[4])

    def test_all_same_values_return_50(self, flat_series):
        result = expanding_percentile_rank(flat_series)
        # All values equal → every rank should be 50
        assert (result.dropna() == pytest.approx(50.0)).all()

    def test_output_range_0_to_100(self, monotone_series):
        result = expanding_percentile_rank(monotone_series)
        non_nan = result.dropna()
        assert (non_nan >= 0).all()
        assert (non_nan <= 100).all()

    def test_last_value_of_descending_series_is_zero(self):
        s = pd.Series(
            [10.0, 8.0, 6.0, 4.0, 2.0],
            index=pd.date_range("2020-01-01", periods=5, freq="MS"),
        )
        result = expanding_percentile_rank(s)
        # 2.0 is minimum of [10, 8, 6, 4, 2] → rank 1 → (1-1)/(5-1)*100 = 0
        assert result.iloc[-1] == pytest.approx(0.0)


# ── apply_direction ───────────────────────────────────────────────────────────

class TestApplyDirection:
    def test_higher_is_better_unchanged(self, monotone_series):
        pct = expanding_percentile_rank(monotone_series)
        directed = apply_direction(pct, higher_is_better=True)
        pd.testing.assert_series_equal(directed, pct)

    def test_flip_sums_to_100(self, monotone_series):
        pct = expanding_percentile_rank(monotone_series)
        flipped = apply_direction(pct, higher_is_better=False)
        both = (pct + flipped).dropna()
        np.testing.assert_allclose(both.values, 100.0, atol=1e-9)

    def test_lower_is_better_reverses_monotone(self, monotone_series):
        pct = expanding_percentile_rank(monotone_series)
        flipped = apply_direction(pct, higher_is_better=False)
        non_nan = flipped.dropna()
        assert non_nan.is_monotonic_decreasing


# ── proximity_2pct_score ──────────────────────────────────────────────────────

class TestProximity2PctScore:
    def test_at_target_gets_highest_score(self):
        """When history is [5%, 8%] and new reading = 2%, should score highest."""
        s = pd.Series(
            [5.0, 8.0, 2.0],
            index=pd.date_range("2022-01-01", periods=3, freq="MS"),
        )
        result = proximity_2pct_score(s, target=2.0)
        assert result.iloc[2] == pytest.approx(100.0)

    def test_farthest_from_target_gets_lowest_score(self):
        """8% is farthest from 2% in [5%, 8%, 2%] history."""
        s = pd.Series(
            [5.0, 8.0, 2.0],
            index=pd.date_range("2022-01-01", periods=3, freq="MS"),
        )
        result = proximity_2pct_score(s, target=2.0)
        # At t=1, history is [5%, 8%]; distance 6% is max → score = 0
        assert result.iloc[1] == pytest.approx(0.0)

    def test_output_range_0_to_100(self, cpi_series):
        result = proximity_2pct_score(cpi_series)
        non_nan = result.dropna()
        assert (non_nan >= 0).all()
        assert (non_nan <= 100).all()

    def test_below_target_same_as_above_target_symmetric(self):
        """1% below target (1%) and 1% above target (3%) should score equally."""
        s = pd.Series(
            [1.0, 3.0],
            index=pd.date_range("2020-01-01", periods=2, freq="MS"),
        )
        result = proximity_2pct_score(s, target=2.0)
        # At t=1: distance(1%) = 1.0, distance(3%) = 1.0 → same rank → same score
        assert result.iloc[0] == pytest.approx(result.iloc[1])


# ── smooth_scores ─────────────────────────────────────────────────────────────

class TestSmoothScores:
    def test_monthly_3period_no_early_nans(self):
        s = pd.Series(
            [60.0, 70.0, 80.0, 50.0, 40.0],
            index=pd.date_range("2020-01-01", periods=5, freq="MS"),
        )
        result = smooth_scores(s, frequency="monthly")
        assert result.notna().all(), "Smoothing with min_periods=1 should not produce NaN"

    def test_quarterly_unchanged(self):
        s = pd.Series(
            [60.0, 70.0, 80.0],
            index=pd.date_range("2020-01-01", periods=3, freq="QS"),
        )
        result = smooth_scores(s, frequency="quarterly")
        pd.testing.assert_series_equal(result, s)

    def test_smoothed_value_within_range(self):
        s = pd.Series(
            [0.0, 100.0, 0.0],
            index=pd.date_range("2020-01-01", periods=3, freq="MS"),
        )
        result = smooth_scores(s, frequency="monthly")
        # Smoothed values should be weighted averages, still in [0, 100]
        assert (result >= 0).all()
        assert (result <= 100).all()


# ── transform functions ───────────────────────────────────────────────────────

class TestTransforms:
    def test_yoy_12_period_lag(self):
        idx = pd.date_range("2019-01-01", periods=24, freq="MS")
        # Level starts at 100 and grows 10% over the year
        levels = [100.0 * (1 + 0.10 / 12) ** i for i in range(24)]
        s = pd.Series(levels, index=idx)
        yoy = transform_yoy(s)
        # After 12 periods, YoY should be ~10%
        assert yoy.iloc[12] == pytest.approx(10.0, abs=0.5)

    def test_yoy_first_12_periods_are_nan(self):
        s = pd.Series(
            range(24),
            index=pd.date_range("2020-01-01", periods=24, freq="MS"),
            dtype=float,
        )
        yoy = transform_yoy(s)
        assert yoy.iloc[:12].isna().all()

    def test_mom_3mo_ann_preserves_length(self):
        s = pd.Series(
            [100.0 + i * 0.5 for i in range(36)],
            index=pd.date_range("2020-01-01", periods=36, freq="MS"),
        )
        result = transform_mom_3mo_ann(s)
        assert len(result) == len(s)


# ── forward_fill_quarterly_to_monthly ────────────────────────────────────────

class TestForwardFill:
    def test_quarterly_fills_3_months(self):
        quarterly = pd.Series(
            [75.0, 80.0],
            index=pd.DatetimeIndex(["2024-01-01", "2024-04-01"]),
        )
        monthly_idx = pd.date_range("2024-01-01", "2024-06-01", freq="MS")
        result = forward_fill_quarterly_to_monthly(quarterly, monthly_idx)
        assert result.notna().all()
        assert result.loc["2024-01-01"] == 75.0
        assert result.loc["2024-02-01"] == 75.0
        assert result.loc["2024-03-01"] == 75.0
        assert result.loc["2024-04-01"] == 80.0

    def test_does_not_fill_beyond_limit(self):
        quarterly = pd.Series(
            [75.0],
            index=pd.DatetimeIndex(["2024-01-01"]),
        )
        # 5-month monthly index: only 3 should be filled (limit=3)
        monthly_idx = pd.date_range("2024-01-01", "2024-05-01", freq="MS")
        result = forward_fill_quarterly_to_monthly(quarterly, monthly_idx, max_fill_periods=3)
        assert result.iloc[0] == 75.0  # Jan
        assert result.iloc[1] == 75.0  # Feb
        assert result.iloc[2] == 75.0  # Mar
        assert result.iloc[3] == 75.0  # Apr (within limit of 3 fills from Jan)
        # May (4 months from Jan) should be NaN — beyond limit
        assert np.isnan(result.iloc[4])


# ── score_indicator end-to-end ────────────────────────────────────────────────

class TestScoreIndicator:
    def test_returns_dataframe_with_required_columns(self):
        obs = [(f"2020-{i:02d}-01", float(i)) for i in range(1, 13)]
        df = score_indicator(obs, higher_is_better=True, scoring_type="percentile", frequency="monthly")
        required = {"score_date", "raw_value", "percentile_rank", "score", "smoothed_score"}
        assert required.issubset(set(df.columns))

    def test_empty_observations_returns_empty_df(self):
        df = score_indicator([], higher_is_better=True, scoring_type="percentile", frequency="monthly")
        assert df.empty

    def test_context_only_returns_nan_scores(self):
        obs = [("2020-01-01", 100.0), ("2020-02-01", 105.0)]
        df = score_indicator(obs, higher_is_better=None, scoring_type="context_only", frequency="monthly")
        assert df["score"].isna().all()

    def test_scores_in_0_100_range(self):
        obs = [(f"2020-{i:02d}-01", float(i * 10)) for i in range(1, 13)]
        df = score_indicator(obs, higher_is_better=True, scoring_type="percentile", frequency="monthly")
        valid = df["score"].dropna()
        assert (valid >= 0).all()
        assert (valid <= 100).all()
