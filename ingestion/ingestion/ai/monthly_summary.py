"""
Claude-powered monthly report generator.

Generates the plain-English monthly narrative for the Consumer Health Score:
  - One-sentence headline interpretation
  - Sub-score breakdown explanation
  - Data vs. corporate narrative gap analysis
  - Biggest movers explanation

Model: claude-sonnet-4-6
"""

from __future__ import annotations

import json
from typing import Any

import structlog

from ingestion.config import settings

log = structlog.get_logger(__name__)

MONTHLY_SUMMARY_PROMPT = """\
You are writing the monthly Consumer Health Score update for The Consumer Compass,
a data-driven dashboard tracking U.S. consumer financial health.

Current score data:
- Headline Score: {headline_score:.1f} / 100 ({band})
- Prior month: {prior_score:.1f} (change: {delta:+.1f})
- Sub-scores: {subscores_json}
- Biggest positive movers: {gains_text}
- Biggest negative movers: {drops_text}

Editorial voice: data-forward, plain-English, slightly skeptical of corporate narratives,
investor-readable. Never partisan. Never doomer. Never cheerleader. Always link to source data.
No more than 3 sentences per paragraph.

Generate:
1. "one_liner": A single sentence (max 40 words) interpreting the current score and its direction.
   Example: "The consumer remains broadly resilient but credit stress indicators are accelerating
   in subprime segments, suggesting the 'healthy consumer' narrative may be getting stale."

2. "narrative_md": 2-3 paragraph Markdown narrative explaining:
   - What the current score means in plain English
   - Which sub-scores are driving the change
   - What to watch in the coming months

3. "data_vs_narrative_md": 1-2 paragraph Markdown section specifically examining where
   corporate commentary (if any) is diverging from the data direction. Be specific about
   which sub-scores are contradicting what companies are saying.

Return ONLY a JSON object with keys: one_liner, narrative_md, data_vs_narrative_md"""


async def generate_monthly_summary(
    headline_score: float,
    band: str,
    prior_score: float,
    subscores: dict[str, float],
    biggest_gains: list[dict],
    biggest_drops: list[dict],
) -> dict[str, str]:
    """
    Generate monthly narrative using Claude.
    Returns {one_liner, narrative_md, data_vs_narrative_md}.
    Falls back to placeholder text if API not configured.
    """
    if not settings.anthropic_api_key:
        log.warning("monthly_summary.skip", reason="ANTHROPIC_API_KEY not set")
        return _placeholder_summary(headline_score, band)

    try:
        import anthropic
    except ImportError:
        return _placeholder_summary(headline_score, band)

    delta = headline_score - prior_score
    gains_text = ", ".join(
        f"{g['label']} (+{g['delta']:.1f})" for g in biggest_gains
    ) or "none"
    drops_text = ", ".join(
        f"{d['label']} ({d['delta']:.1f})" for d in biggest_drops
    ) or "none"

    prompt = MONTHLY_SUMMARY_PROMPT.format(
        headline_score=headline_score,
        band=band,
        prior_score=prior_score,
        delta=delta,
        subscores_json=json.dumps(
            {k: round(v, 1) for k, v in subscores.items()}, indent=2
        ),
        gains_text=gains_text,
        drops_text=drops_text,
    )

    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    try:
        message = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=2048,
            messages=[{"role": "user", "content": prompt}],
        )
        import re
        raw = message.content[0].text
        clean = re.sub(r"```(?:json)?\s*", "", raw).strip()
        result = json.loads(clean)
        return result
    except Exception as exc:
        log.error("monthly_summary.error", error=str(exc))
        return _placeholder_summary(headline_score, band)


def _placeholder_summary(score: float, band: str) -> dict[str, str]:
    return {
        "one_liner": f"The Consumer Health Score stands at {score:.0f} ({band}).",
        "narrative_md": "_Monthly narrative pending — set ANTHROPIC_API_KEY to enable AI summaries._",
        "data_vs_narrative_md": "_Data vs. narrative analysis pending._",
    }
