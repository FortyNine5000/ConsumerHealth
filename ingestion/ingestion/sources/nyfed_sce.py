"""
NY Fed Survey of Consumer Expectations (SCE) data source.

Key series: Probability of Missing a Minimum Debt Payment (next 3 months).
Published monthly on the second Monday of each month.

Source: newyorkfed.org/microeconomics/sce
Data: Free public Fed data, freely distributable.
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

SCE_BASE = "https://www.newyorkfed.org/microeconomics/sce"
# The SCE publishes an XLSX with the full time series data
SCE_XLSX_URL = "https://www.newyorkfed.org/medialibrary/interactives/sce/sce/downloads/data/FRBNY-SCE-Public-Microdata-Complete-13-16.xlsx"
# Newer datasets also available; the page lists multiple XLSXs


class NYFedSCEScraper:
    """Scraper for NY Fed SCE monthly data."""

    def __init__(self) -> None:
        self._http: httpx.AsyncClient | None = None

    async def __aenter__(self) -> "NYFedSCEScraper":
        self._http = httpx.AsyncClient(
            timeout=60.0,
            headers={"User-Agent": "ConsumerCompass/1.0 (admin@example.com)"},
            follow_redirects=True,
        )
        return self

    async def __aexit__(self, *args: Any) -> None:
        if self._http:
            await self._http.aclose()

    async def find_chart_data_url(self) -> str | None:
        """
        Scrape the SCE landing page to find the chart data download URL.
        The NY Fed publishes JSON or CSV chart data for the interactive charts.
        """
        if self._http is None:
            raise RuntimeError("Must be used as async context manager")
        try:
            resp = await self._http.get(SCE_BASE)
            resp.raise_for_status()
            html = resp.text
            # Look for data download links (JSON or CSV)
            xlsx_links = re.findall(r'href="([^"]*(?:sce|SCE)[^"]*\.(?:xlsx|csv|json))"', html)
            if xlsx_links:
                link = xlsx_links[0]
                if link.startswith("http"):
                    return link
                return f"https://www.newyorkfed.org{link}"
        except Exception as exc:
            log.warning("nyfed_sce.find_url.error", error=str(exc))
        return None

    @retry(
        wait=wait_exponential(multiplier=1, min=2, max=60),
        stop=stop_after_attempt(3),
        retry=retry_if_exception_type((httpx.HTTPError, httpx.TimeoutException)),
        reraise=True,
    )
    async def fetch_missing_payment_series(self) -> list[dict[str, Any]]:
        """
        Attempt to fetch the monthly SCE missing-payment probability series.

        Returns [{date: "YYYY-MM-DD", value: float}].

        Note: The NY Fed publishes this as an interactive chart. The underlying
        data is accessible via their chart data API. If the structured endpoint
        is not available, returns empty list and logs a warning to prompt manual review.
        """
        if self._http is None:
            raise RuntimeError("Must be used as async context manager")

        # Try the known SCE summary statistics endpoint
        endpoints_to_try = [
            "https://www.newyorkfed.org/medialibrary/interactives/sce/sce/downloads/data/sce_public_chartdata.xlsx",
            "https://www.newyorkfed.org/microeconomics/sce/download",
        ]

        for url in endpoints_to_try:
            try:
                resp = await self._http.get(url)
                if resp.status_code == 200:
                    content_type = resp.headers.get("content-type", "")
                    if "excel" in content_type or "spreadsheet" in content_type or url.endswith(".xlsx"):
                        return self._parse_xlsx_missing_payment(resp.content)
            except Exception:
                continue

        log.warning(
            "nyfed_sce.fetch.no_data",
            msg="SCE missing-payment series not auto-fetched. Manual download required from newyorkfed.org/microeconomics/sce",
        )
        return []

    def _parse_xlsx_missing_payment(self, xlsx_bytes: bytes) -> list[dict[str, Any]]:
        """Parse SCE XLSX for missing minimum payment probability series."""
        try:
            import io
            import pandas as pd

            xl = pd.ExcelFile(io.BytesIO(xlsx_bytes))
            results = []

            for sheet_name in xl.sheet_names:
                if "miss" in sheet_name.lower() or "payment" in sheet_name.lower() or "debt" in sheet_name.lower():
                    df = pd.read_excel(io.BytesIO(xlsx_bytes), sheet_name=sheet_name)
                    # Look for date column and probability column
                    for col in df.columns:
                        if "date" in str(col).lower() or "period" in str(col).lower():
                            date_col = col
                            break
                    else:
                        continue

                    for _, row in df.iterrows():
                        try:
                            date_val = row[date_col]
                            if hasattr(date_val, "strftime"):
                                date_str = date_val.strftime("%Y-%m-%d")
                            else:
                                date_str = str(date_val)[:10]
                            # Find a numeric value column
                            for vcol in df.columns:
                                if vcol == date_col:
                                    continue
                                try:
                                    value = float(row[vcol])
                                    results.append({"date": date_str, "value": value})
                                    break
                                except (ValueError, TypeError):
                                    continue
                        except Exception:
                            continue
                    if results:
                        break

            return sorted(results, key=lambda x: x["date"])
        except Exception as exc:
            log.error("nyfed_sce.parse.error", error=str(exc))
            return []


async def ingest_missing_payment(client: "libsql_client.Client") -> int:
    """Download and ingest NY Fed SCE missing-payment probability series."""
    from ingestion.db import get_all_indicators, upsert_observations

    today = datetime.date.today().isoformat()
    all_indicators = await get_all_indicators(client)
    indicator_id = next(
        (ind["id"] for ind in all_indicators if ind["series_id"] == "NYFED_SCE_MISS_PAYMENT"),
        None,
    )
    if indicator_id is None:
        log.warning("nyfed_sce.ingest.skip", reason="NYFED_SCE_MISS_PAYMENT not in indicators")
        return 0

    async with NYFedSCEScraper() as scraper:
        try:
            observations = await scraper.fetch_missing_payment_series()
        except Exception as exc:
            log.error("nyfed_sce.ingest.error", error=str(exc))
            return 0

    if not observations:
        return 0

    rows = [(obs["date"], obs["value"]) for obs in observations]
    n = await upsert_observations(client, indicator_id, rows, vintage_date=today)
    log.info("nyfed_sce.ingest.ok", rows=n)
    return n
