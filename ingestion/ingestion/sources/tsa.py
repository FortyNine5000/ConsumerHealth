"""
TSA Checkpoint Throughput scraper.

Scrapes daily passenger volumes from tsa.gov/travel/passenger-volumes.
Published ~9am ET Monday-Friday for prior day(s).

We compute a 7-day rolling average and express as % of same-day 2019 baseline.
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

TSA_URL = "https://www.tsa.gov/travel/passenger-volumes"


class TSAScraper:
    """HTML scraper for TSA daily passenger volume data."""

    def __init__(self) -> None:
        self._http: httpx.AsyncClient | None = None

    async def __aenter__(self) -> "TSAScraper":
        self._http = httpx.AsyncClient(
            timeout=30.0,
            headers={"User-Agent": "Mozilla/5.0 ConsumerCompass/1.0"},
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
    async def fetch_raw(self) -> str:
        if self._http is None:
            raise RuntimeError("TSAScraper must be used as async context manager")
        resp = await self._http.get(TSA_URL)
        resp.raise_for_status()
        return resp.text

    def parse(self, html: str) -> list[dict[str, Any]]:
        """
        Parse the TSA throughput table from HTML.
        Returns list of {date: "YYYY-MM-DD", current_travelers: int, travelers_2019: int}.

        The TSA page uses a table with columns:
          Date | 2024 (or current year) | 2023 | 2022 | 2021 | 2020 | 2019
        """
        # Find all table rows with date and traveler counts
        # Pattern: date like "1/1/2024" followed by numbers with commas
        pattern = re.compile(
            r"(\d{1,2}/\d{1,2}/(\d{4}))\s*\|?\s*([\d,]+)\s*\|?\s*([\d,]+)?"
        )
        rows = []
        for match in pattern.finditer(html):
            date_str = match.group(1)
            current_year = int(match.group(2))
            current_travelers = int(match.group(3).replace(",", ""))
            # We need the 2019 column — this is tricky since the number of
            # columns changes. For now, store raw current and flag for transform.
            try:
                dt = datetime.datetime.strptime(date_str, "%m/%d/%Y")
                rows.append({
                    "date": dt.strftime("%Y-%m-%d"),
                    "current_travelers": current_travelers,
                    "year": current_year,
                })
            except ValueError:
                continue

        return sorted(rows, key=lambda x: x["date"])


async def ingest_throughput(client: "libsql_client.Client") -> int:
    """
    Scrape TSA throughput and upsert as % of 2019 baseline to indicator_observations.

    Note: Requires 2019 data to already be present to compute the ratio.
    For initial backfill, raw values are stored; ratio is computed in transforms.
    """
    from ingestion.db import get_all_indicators, upsert_observations

    today = datetime.date.today().isoformat()
    all_indicators = await get_all_indicators(client)
    indicator_id = next(
        (ind["id"] for ind in all_indicators if ind["series_id"] == "TSA_THROUGHPUT_VS2019"),
        None,
    )
    if indicator_id is None:
        log.warning("tsa.ingest.skip", reason="TSA_THROUGHPUT_VS2019 not in indicators table")
        return 0

    async with TSAScraper() as scraper:
        try:
            html = await scraper.fetch_raw()
            raw_data = scraper.parse(html)
        except Exception as exc:
            log.error("tsa.fetch.error", error=str(exc))
            return 0

    if not raw_data:
        log.warning("tsa.ingest.no_data")
        return 0

    # Store raw traveler counts; the percentile transform handles the ratio computation
    # Store value as raw count (will be transformed to % of 2019 in scoring pipeline)
    rows = [(r["date"], float(r["current_travelers"])) for r in raw_data]
    n = await upsert_observations(client, indicator_id, rows, vintage_date=today)
    log.info("tsa.ingest.ok", rows=n)
    return n
