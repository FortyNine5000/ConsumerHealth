import pandas as pd

from ingestion.reports.monthly_monitor import build_consumer_weakness_monitor


def test_build_consumer_weakness_monitor_returns_report_payload():
    headline_df = pd.DataFrame([
        {
            "score_date": "2026-05-01",
            "score": 52.4,
            "band": "Weakening",
        }
    ])
    headline_signal = pd.DataFrame([
        {
            "score_date": "2026-05-01",
            "stable_v2_score": 48.0,
            "turning_point_score": 39.0,
            "momentum_score": 28.0,
            "trend_score": 41.0,
        }
    ])
    subscore_signal = pd.DataFrame([
        {
            "slug": "credit_stress",
            "score_date": "2026-05-01",
            "state_score": 45.0,
            "momentum_score": 20.0,
            "trend_score": 35.0,
            "turning_point_score": 32.0,
            "weakening_count": 2,
        }
    ])
    indicator_signal = pd.DataFrame([
        {
            "indicator_slug": "drcclacbs",
            "score_date": "2026-05-01",
            "state_score": 40.0,
            "momentum_score": 15.0,
            "trend_score": 25.0,
            "short_delta": -12.0,
        }
    ])
    meta = {
        "drcclacbs": {
            "name": "Credit card delinquency rate",
            "subscore": "credit_stress",
            "frequency": "quarterly",
        }
    }

    report = build_consumer_weakness_monitor(
        "2026-05-01",
        headline_df,
        headline_signal,
        subscore_signal,
        indicator_signal,
        indicator_meta=meta,
    )

    assert report is not None
    assert report["stance"] == "weakening"
    assert report["slug"] == "consumer-weakness-monitor-2026-05"
    assert report["top_weakening_indicators"][0]["label"] == "Credit card delinquency rate"
    assert "Production headline score: 52.4" in report["summary_md"]
