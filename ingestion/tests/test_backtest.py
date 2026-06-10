import pandas as pd

from ingestion.analytics.backtest import (
    compute_forward_outcome_metrics,
    compute_recession_metrics,
    render_validation_markdown,
)


def test_forward_outcome_metrics_measure_candidate_relationships():
    dates = pd.date_range("2020-01-01", periods=24, freq="MS")
    scores = [100 - (idx * 4) for idx in range(24)]
    candidate = pd.DataFrame({
        "score_date": dates.strftime("%Y-%m-%d"),
        "v1_score": scores,
        "stable_v2_score": scores,
        "turning_point_score": scores,
    })
    outcomes = pd.DataFrame({
        "score_date": dates.strftime("%Y-%m-%d"),
        "real_pce_mom_ann": [100 - (idx * idx) for idx in range(24)],
    })

    metrics = compute_forward_outcome_metrics(candidate, outcomes, horizon_months=3)

    row = metrics[
        (metrics["candidate"] == "turning_point")
        & (metrics["outcome_slug"] == "real_pce_mom_ann")
    ].iloc[0]
    assert row["corr_score_to_forward_change"] is not None
    assert row["warning_count"] > 0


def test_recession_metrics_include_known_recession_when_history_exists():
    dates = pd.date_range("2007-01-01", periods=36, freq="MS")
    scores = [80.0] * 11 + [70.0, 65.0, 60.0, 55.0, 50.0, 45.0, 40.0] + [42.0] * 18
    candidate = pd.DataFrame({
        "score_date": dates.strftime("%Y-%m-%d"),
        "v1_score": scores,
        "stable_v2_score": scores,
        "turning_point_score": scores,
    })

    metrics = compute_recession_metrics(candidate)

    gfc = metrics[(metrics["candidate"] == "v1") & (metrics["recession"] == "GFC")]
    assert not gfc.empty
    assert gfc.iloc[0]["drawdown_start_to_trough"] >= 20


def test_render_validation_markdown_includes_sections():
    markdown = render_validation_markdown(pd.DataFrame(), pd.DataFrame(), generated_at="2026-06-01")

    assert "# Consumer Compass Signal Validation" in markdown
    assert "Forward Outcome Fit" in markdown
    assert "Recession Response" in markdown
