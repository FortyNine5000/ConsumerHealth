"""
Manheim Used Vehicle Value Index (MUVVI) scraper.

Published on the 5th business day of each month.
Source: publish.manheim.com / coxautoinc.com

Context-only indicator — not scored directionally in v1.
Displayed on the Big-Ticket Affordability indicator page.
"""

from __future__ import annotations

import asyncio
import datetime
import re
from typing import Any

import httpx
import structlog
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

log = structlog.get_logger(__name__)

MANHEIM_URL = "https://publish.manheim.com/en/services/consulting/used-vehicle-value-index.html"


class ManheimScraper:
    """Scraper for Manheim Used Vehicle Value Index."""

    def __init__(self) -> None:
        self._http: httpx.AsyncClient | None = None

    async def __aenter__(self) -> "ManheimScraper":
        self._http = httpx.AsyncClient(
            timeout=30.0,
            headers={
                "User-Agent": "Mozilla/5.0 ConsumerCompass/1.0",
                "Accept": "text/html,application/xhtml+xml",
            },
            follow_redirects=True,
        )
        return self

    async def __aexit__(self, *args: Any) -> None:
        if self._http:
            await self._http.aclose()

    @retry(
        wait=wait_exponential(multiplier=1, min=2, max=30),
        stop=stop_after_attempt(3),
        retry=retry_if_exception_type((httpx.HTTPError, httpx.TimeoutException)),
        reraise=True,
    )
    async def fetch_index_value(self) -> dict[str, Any] | None:
        """
        Attempt to scrape the current MUVVI value from the Manheim page.

        Returns {date: "YYYY-MM-DD", value: float} or None if not parseable.
        Note: Manheim may require a login or JavaScript rendering to access
        full data. If scraping fails, log a warning for manual entry.
        """
        if self._http is None:
            raise RuntimeError("Must be used as async context manager")

        try:
            resp = await self._http.get(MANHEIM_URL)
            resp.raise_for_status()
            html = resp.text
        except Exception as exc:
            log.warning("manheim.fetch.error", error=str(exc))
            return None

        # Try to extract index value from HTML
        # Manheim typically shows "Index: XXX.X" or similar pattern
        patterns = [
            r"(?:MUVVI|index)\s*[:\s]+(\d{2,3}(?:\.\d{1,2})?)",
            r"(\d{2,3}\.\d{1,2})\s*(?:in|for)\s+(?:January|February|March|April|May|June|July|August|September|October|November|December)",
        ]
        for pattern in patterns:
            m = re.search(pattern, html, re.IGNORECASE)
            if m:
                try:
                    value = float(m.group(1))
                    today = datetime.date.today()
                    date_str = today.replace(day=1).isoformat()
                    return {"date": date_str, "value": value}
                except ValueError:
                    continue

        log.warning(
            "manheim.fetch.no_data",
            msg="Could not parse MUVVI from page. Manual entry may be required.",
        )
        return None


async def ingest_muvvi(client: "libsql_client.Client") -> int:
    """Scrape and ingest Manheim MUVVI value."""
    from ingestion.db import get_all_indicators, upsert_observations

    today = datetime.date.today().isoformat()
    all_indicators = await get_all_indicators(client)
    indicator_id = next(
        (ind["id"] for ind in all_indicators if ind["series_id"] == "MANHEIM_MUVVI"),
        None,
    )
    if indicator_id is None:
        log.warning("manheim.ingest.skip", reason="MANHEIM_MUVVI not in indicators table")
        return 0

    async with ManheimScraper() as scraper:
        result = await scraper.fetch_index_value()

    if not result:
        return 0

    rows = [(result["date"], result["value"])]
    n = await upsert_observations(client, indicator_id, rows, vintage_date=today)
    log.info("manheim.ingest.ok", rows=n, value=result["value"])
    return n
