"""
Deterministic Consumer Weakness Monitor draft generation.

The monitor is intentionally built from persisted score components. It is the
monthly editorial layer on top of the proof engine, not a replacement for it.
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from ingestion.transforms.signals import rank_weakening_indicators


SUBSCORE_LABELS = {
    "labor_income": "Labor & Income",
    "household_balance_sheet": "Household Balance Sheet",
    "credit_stress": "Credit Stress",
    "spending_demand": "Spending & Demand",
    "sentiment_expectations": "Sentiment & Expectations",
    "inflation_affordability": "Inflation & Affordability",
    "big_ticket_affordability": "Big-Ticket Affordability",
}


def _num(value: Any, digits: int = 1) -> float | None:
    if value is None or pd.isna(value):
        return None
    return round(float(value), digits)


def _fmt(value: Any, digits: int = 1) -> str:
    number = _num(value, digits)
    return "n/a" if number is None else f"{number:.{digits}f}"


def _stance(momentum_score: float | None, turning_point_score: float | None) -> str:
    if momentum_score is None and turning_point_score is None:
        return "watchful"
    if (momentum_score is not None and momentum_score < 35) or (
        turning_point_score is not None and turning_point_score < 45
    ):
        return "weakening"
    if (momentum_score is not None and momentum_score < 50) or (
        turning_point_score is not None and turning_point_score < 55
    ):
        return "watchful"
    return "stable"


def _headline_for_stance(stance: str, score_date: str) -> str:
    month = pd.Timestamp(score_date).strftime("%B %Y")
    if stance == "weakening":
        return f"Consumer Weakness Monitor: deterioration signals are building in {month}"
    if stance == "watchful":
        return f"Consumer Weakness Monitor: mixed signals warrant a closer watch in {month}"
    return f"Consumer Weakness Monitor: consumer weakness signals remain contained in {month}"


def _top_weak_subscores(subscore_components: pd.DataFrame, score_date: str, top_n: int = 3) -> list[dict]:
    if subscore_components.empty:
        return []
    date_df = subscore_components[subscore_components["score_date"] == score_date].copy()
    if date_df.empty:
        return []
    date_df["sort_momentum"] = pd.to_numeric(date_df["momentum_score"], errors="coerce")
    date_df = date_df.sort_values("sort_momentum", ascending=True, na_position="last")

    results = []
    for _, row in date_df.head(top_n).iterrows():
        slug = str(row["slug"])
        results.append({
            "slug": slug,
            "label": SUBSCORE_LABELS.get(slug, slug),
            "state_score": _num(row.get("state_score")),
            "momentum_score": _num(row.get("momentum_score")),
            "trend_score": _num(row.get("trend_score")),
            "turning_point_score": _num(row.get("turning_point_score")),
            "weakening_count": int(row.get("weakening_count") or 0),
        })
    return results


def _attach_indicator_labels(
    rows: list[dict],
    indicator_meta: dict[str, dict[str, Any]] | None,
) -> list[dict]:
    indicator_meta = indicator_meta or {}
    labeled = []
    for row in rows:
        slug = str(row["indicator_slug"])
        meta = indicator_meta.get(slug, {})
        labeled.append({
            **row,
            "label": meta.get("name") or meta.get("label") or slug,
            "subscore": meta.get("subscore"),
            "frequency": meta.get("frequency"),
        })
    return labeled


def build_consumer_weakness_monitor(
    score_date: str,
    headline_df: pd.DataFrame,
    headline_signal_components: pd.DataFrame,
    subscore_signal_components: pd.DataFrame,
    indicator_signal_components: pd.DataFrame,
    indicator_meta: dict[str, dict[str, Any]] | None = None,
    top_n: int = 5,
) -> dict[str, Any] | None:
    """Build a monitor draft for one score date."""
    headline_row = headline_df[headline_df["score_date"] == score_date]
    signal_row = headline_signal_components[
        headline_signal_components["score_date"] == score_date
    ]
    if headline_row.empty or signal_row.empty:
        return None

    h = headline_row.iloc[0]
    s = signal_row.iloc[0]
    stance = _stance(_num(s.get("momentum_score")), _num(s.get("turning_point_score")))
    top_indicators = _attach_indicator_labels(
        rank_weakening_indicators(indicator_signal_components, score_date, top_n=top_n),
        indicator_meta,
    )
    top_subscores = _top_weak_subscores(subscore_signal_components, score_date)

    slug = f"consumer-weakness-monitor-{pd.Timestamp(score_date).strftime('%Y-%m')}"
    headline = _headline_for_stance(stance, score_date)

    bullets = []
    for indicator in top_indicators[:3]:
        bullets.append(
            f"- {indicator['label']}: 3-month score change {_fmt(indicator.get('short_delta'))}, "
            f"momentum {_fmt(indicator.get('momentum_score'))}."
        )
    if not bullets:
        bullets.append("- No indicator-level weakening drivers are available for this date yet.")

    subscore_text = ", ".join(
        f"{row['label']} ({_fmt(row['momentum_score'])})" for row in top_subscores
    ) or "n/a"

    summary_md = "\n".join([
        f"# {headline}",
        "",
        f"Production headline score: {_fmt(h.get('score'))} ({h.get('band', 'n/a')}).",
        f"Stable v2 candidate: {_fmt(s.get('stable_v2_score'))}; turning-point candidate: {_fmt(s.get('turning_point_score'))}.",
        f"Headline momentum component: {_fmt(s.get('momentum_score'))}; trend component: {_fmt(s.get('trend_score'))}.",
        "",
        "## What changed",
        *bullets,
        "",
        "## Weakest subscore momentum",
        subscore_text,
        "",
        "## Editorial posture",
        (
            "Lead with consumer weakening risk and cite the deteriorating indicators."
            if stance == "weakening"
            else "Lead with divergence and identify the next releases that could confirm or reject the signal."
            if stance == "watchful"
            else "Lead with resilience, while keeping the weakening watchlist visible."
        ),
    ])

    return {
        "score_date": score_date,
        "slug": slug,
        "headline": headline,
        "stance": stance,
        "headline_score": _num(h.get("score")),
        "stable_v2_score": _num(s.get("stable_v2_score")),
        "turning_point_score": _num(s.get("turning_point_score")),
        "momentum_score": _num(s.get("momentum_score")),
        "trend_score": _num(s.get("trend_score")),
        "top_weakening_indicators": top_indicators,
        "top_weak_subscores": top_subscores,
        "summary_md": summary_md,
    }
