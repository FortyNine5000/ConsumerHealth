"""
Claude-powered earnings-call quote classifier (Phase 3).

Takes SEC 8-K Exhibit 99 text and extracts consumer-health-relevant quotes,
classifying each against the 26-tag taxonomy in spec §8.3.

Model: claude-sonnet-4-6 (fast, cost-effective for structured extraction).
"""

from __future__ import annotations

import json
import re
from typing import Any

import structlog

from ingestion.config import settings

log = structlog.get_logger(__name__)

# The 26 taxonomy tags from spec §8.3
TAXONOMY_TAGS = [
    "consumer_resilience",
    "consumer_weakness",
    "lower_income_pressure",
    "middle_income_pressure",
    "high_income_resilience",
    "trade_down",
    "trading_up",
    "promotional_sensitivity",
    "credit_normalization",
    "delinquency_pressure",
    "chargeoff_pressure",
    "payment_stress",
    "demand_elasticity",
    "big_ticket_weakness",
    "durable_goods_weakness",
    "travel_strength",
    "restaurant_weakness",
    "grocery_pressure",
    "housing_affordability",
    "auto_affordability",
    "bnpl_signals",
    "wage_labor_pressure",
    "inflation_fatigue",
    "bifurcated_consumer",
    "regional_divergence",
    "management_spin_narrative_risk",
]

CLASSIFICATION_PROMPT = """\
You are analyzing an earnings call transcript excerpt from {company} ({ticker}), {fiscal_quarter}.

Extract every quote (50–150 words each) that contains a SUBSTANTIVE claim about U.S. consumer financial health.

For each quote, provide a JSON object with:
- "quote_text": the exact quote (50–150 words, verbatim)
- "speaker_name": speaker's name if identifiable
- "speaker_title": speaker's title (CEO, CFO, etc.) if identifiable
- "category": array of relevant tags from: {tags}
- "sentiment_score": integer -2 (very negative) to +2 (very positive) about consumer health
- "consumer_segment": one of "lower", "middle", "high", "all"
- "metric_referenced": primary metric discussed (e.g. "delinquency", "spend", "credit_quality")
- "agrees_with_dashboard": null (you don't have current dashboard data; leave null)
- "ai_summary": one sentence summarizing the claim

Return a JSON array. If no relevant quotes exist, return [].

IMPORTANT RULES:
- Quotes must be verbatim from the source text
- Each quote must be 50–150 words (fair-use excerpt)
- Focus on substantive consumer financial health claims, not generic business commentary
- Flag management_spin_narrative_risk if the framing seems to minimize clear negative data

Source text:
---
{exhibit_text}
---

Return ONLY the JSON array, no other text."""


async def classify_exhibit(
    ticker: str,
    company: str,
    fiscal_quarter: str,
    exhibit_text: str,
    max_chars: int = 50_000,
) -> list[dict[str, Any]]:
    """
    Use Claude to extract and classify consumer-health quotes from an exhibit.

    Returns list of quote dicts ready for earnings_quotes table insertion.
    Returns empty list if Anthropic API key is not configured.
    """
    if not settings.anthropic_api_key:
        log.warning("quote_classifier.skip", reason="ANTHROPIC_API_KEY not set")
        return []

    try:
        import anthropic
    except ImportError:
        log.error("quote_classifier.error", reason="anthropic package not installed")
        return []

    # Truncate exhibit text to avoid token limits
    truncated_text = exhibit_text[:max_chars]
    if len(exhibit_text) > max_chars:
        log.info("quote_classifier.truncated", original=len(exhibit_text), used=max_chars)

    prompt = CLASSIFICATION_PROMPT.format(
        company=company,
        ticker=ticker,
        fiscal_quarter=fiscal_quarter,
        tags=", ".join(TAXONOMY_TAGS),
        exhibit_text=truncated_text,
    )

    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)

    try:
        message = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=4096,
            messages=[{"role": "user", "content": prompt}],
        )
        raw_response = message.content[0].text
    except Exception as exc:
        log.error("quote_classifier.api_error", ticker=ticker, error=str(exc))
        return []

    # Parse JSON response
    try:
        # Strip markdown code fences if present
        clean = re.sub(r"```(?:json)?\s*", "", raw_response).strip()
        quotes = json.loads(clean)
        if not isinstance(quotes, list):
            log.warning("quote_classifier.bad_format", ticker=ticker)
            return []
    except json.JSONDecodeError as exc:
        log.warning("quote_classifier.json_error", ticker=ticker, error=str(exc))
        return []

    log.info("quote_classifier.ok", ticker=ticker, quotes_extracted=len(quotes))
    return quotes
