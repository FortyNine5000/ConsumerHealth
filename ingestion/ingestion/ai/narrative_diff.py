"""
Data vs. Narrative gap detector.

Compares the direction of each sub-score with the sentiment direction
of recent earnings quotes for related companies, flagging contradictions.

This is the editorial core of the "Data vs. Narrative" feature on the homepage.
"""

from __future__ import annotations

from typing import Any

import structlog

log = structlog.get_logger(__name__)

# Sub-score → related sectors for quote matching
SUBSCORE_SECTOR_MAP: dict[str, list[str]] = {
    "credit_stress":           ["Banks / Card Issuers", "Payment Networks"],
    "spending_demand":         ["Mass Retail / Discount", "Restaurants / Delivery"],
    "big_ticket_affordability":["Autos / Auto Credit", "Housing / Builders"],
    "sentiment_expectations":  ["Banks / Card Issuers", "Mass Retail / Discount"],
    "labor_income":            ["Mass Retail / Discount", "Restaurants / Delivery"],
    "inflation_affordability": ["Mass Retail / Discount", "Grocery"],
    "household_balance_sheet": ["Banks / Card Issuers", "Payment Networks"],
}


def detect_contradictions(
    subscore_deltas: dict[str, float],
    recent_quotes: list[dict[str, Any]],
    delta_threshold: float = 3.0,
    contradiction_sentiments: tuple[int, ...] = (1, 2),
) -> list[dict[str, Any]]:
    """
    Find quotes that contradict the direction of their related sub-score.

    A contradiction occurs when:
    - A sub-score is dropping (delta < -delta_threshold) AND
    - A related company quote has positive sentiment (1 or 2)
    OR:
    - A sub-score is rising significantly (delta > +delta_threshold) AND
    - A related company quote is negative (-1 or -2)

    Args:
        subscore_deltas: {slug: delta_1m} for each sub-score
        recent_quotes: list of quote dicts from earnings_quotes table
        delta_threshold: minimum |delta| to flag as meaningful
        contradiction_sentiments: sentiment scores that contradict a declining sub-score

    Returns:
        List of {quote, subscore_slug, subscore_delta, contradiction_type} dicts
    """
    contradictions = []

    for subscore_slug, delta in subscore_deltas.items():
        if abs(delta) < delta_threshold:
            continue  # Sub-score not moving meaningfully

        related_sectors = SUBSCORE_SECTOR_MAP.get(subscore_slug, [])

        for quote in recent_quotes:
            # Check if quote is from a related sector
            quote_sector = quote.get("sector", "")
            if not any(s.lower() in quote_sector.lower() for s in related_sectors):
                continue

            sentiment = quote.get("sentiment_score", 0)
            contradiction_type = None

            if delta < -delta_threshold and sentiment in contradiction_sentiments:
                contradiction_type = "positive_narrative_vs_declining_data"
            elif delta > delta_threshold and sentiment in (-1, -2):
                contradiction_type = "negative_narrative_vs_improving_data"

            if contradiction_type:
                contradictions.append({
                    "quote": quote,
                    "subscore_slug": subscore_slug,
                    "subscore_delta": round(delta, 1),
                    "contradiction_type": contradiction_type,
                })

    return contradictions


def format_contradiction_summary(
    contradictions: list[dict[str, Any]],
    subscore_labels: dict[str, str] | None = None,
) -> str:
    """Return a plain-English summary of contradictions for the homepage widget."""
    if not contradictions:
        return "Corporate commentary broadly aligns with the data this month."

    labels = subscore_labels or {
        "credit_stress": "Credit Stress",
        "spending_demand": "Spending & Demand",
        "big_ticket_affordability": "Big-Ticket Affordability",
        "sentiment_expectations": "Consumer Sentiment",
        "labor_income": "Labor & Income",
        "inflation_affordability": "Inflation & Affordability",
        "household_balance_sheet": "Household Balance Sheet",
    }

    lines = []
    for c in contradictions[:5]:  # cap at 5 for homepage
        slug = c["subscore_slug"]
        delta = c["subscore_delta"]
        ticker = c["quote"].get("ticker", "?")
        speaker = c["quote"].get("speaker_title", "Management")
        quote_preview = c["quote"].get("quote_text", "")[:80] + "…"
        label = labels.get(slug, slug)
        lines.append(
            f"• {ticker} {speaker}: \"{quote_preview}\" "
            f"— while {label} is {'falling' if delta < 0 else 'rising'} "
            f"({delta:+.1f} pts this month) ⚠"
        )

    count = len(contradictions)
    header = (
        f"{count} quote{'s' if count != 1 else ''} from the last 30 days "
        f"{'contradict' if count != 1 else 'contradicts'} the data direction:"
    )
    return header + "\n" + "\n".join(lines)
