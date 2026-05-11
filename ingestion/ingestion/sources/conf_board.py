"""
Conference Board Consumer Confidence — citation-only module.

IMPORTANT LEGAL NOTE:
The Conference Board CCI data carries explicit redistribution restrictions.
Their data pages bear the watermark:
  "THESE DATA ARE FOR ANALYSIS PURPOSES ONLY.
   NOT FOR REDISTRIBUTION, PUBLISHING, DATABASING OR PUBLIC POSTING
   WITHOUT EXPRESS WRITTEN PERMISSION."

Their Terms of Use add: "You may not reproduce, distribute … display, perform,
create derivative works of, sell, license, extract for use in a database,
or otherwise use any materials."

COMPLIANT PATTERN (per spec §5):
  1. Cite the headline number in editorial text with a link to the press release.
  2. Display FRED proxy CSCICP03USM665S (OECD Consumer Confidence) for the chart.
  3. Do NOT store Conference Board data in the database.
  4. Do NOT display their data as a chart.

This module provides utilities to link to the latest press release
and retrieve the OECD proxy from FRED (which is handled in fred.py).
"""

from __future__ import annotations

import structlog

log = structlog.get_logger(__name__)

CONF_BOARD_PRESS_URL = "https://www.conference-board.org/topics/consumer-confidence/"
FRED_PROXY_SERIES_ID = "CSCICP03USM665S"


def get_latest_press_release_url() -> str:
    """Return the Conference Board consumer confidence press release URL."""
    return CONF_BOARD_PRESS_URL


def get_citation_text(headline_value: float | None = None) -> str:
    """
    Return compliant citation text for the Conference Board CCI.

    headline_value: if provided (manually entered from press release), include it.
    """
    if headline_value is not None:
        return (
            f"Conference Board Consumer Confidence: {headline_value:.1f}. "
            f"Source: The Conference Board. "
            f"Chart uses OECD Consumer Confidence proxy (FRED: CSCICP03USM665S)."
        )
    return (
        "Conference Board Consumer Confidence. "
        "Source: The Conference Board. "
        "Chart uses OECD Consumer Confidence proxy (FRED: CSCICP03USM665S)."
    )


# Note: The OECD proxy (CSCICP03USM665S) is fetched via fred.py — no separate
# ingestion needed here. This module exists solely as documentation of the
# legal constraint and to provide the citation helpers.
log.debug("conf_board: CCI redistribution restricted — using OECD proxy CSCICP03USM665S")
