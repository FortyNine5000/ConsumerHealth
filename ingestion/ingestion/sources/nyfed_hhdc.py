"""
NY Fed Household Debt and Credit (HHDC) data source.

Published quarterly (~3 months after quarter end):
  Q1 → May, Q2 → August, Q3 → November, Q4 → February

Data: credit card transition into serious delinquency (90+ days past due).
Source: XLSX download from newyorkfed.org/microeconomics/hhdc

The HHDC is the single best leading indicator for consumer credit stress.
It is not available on FRED.
"""

from __future__ import annotations

import asyncio
import datetime
import io
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

# Base URL for the HHDC interactive page
HHDC_BASE = "https://www.newyorkfed.org/microeconomics/hhdc"

# Direct XLSX URL pattern (changes with each release — must scrape to find current URL)
HHDC_XLSX_PATTERN = "https://www.newyorkfed.org/medialibrary/interactives/householdcredit/data/xls/"


class NYFedHHDCScraper:
    """Scraper for NY Fed HHDC quarterly XLSX data."""

    def __init__(self) -> None:
        self._http: httpx.AsyncClient | None = None

    async def __aenter__(self) -> "NYFedHHDCScraper":
        self._http = httpx.AsyncClient(
            timeout=60.0,
            headers={"User-Agent": "ConsumerCompass/1.0 (admin@example.com)"},
            follow_redirects=True,
        )
        return self

    async def __aexit__(self, *args: Any) -> None:
        if self._http:
            await self._http.aclose()

    async def find_current_xlsx_url(self) -> str | None:
        """Scrape the HHDC page to find the current XLSX download URL."""
        if self._http is None:
            raise RuntimeError("Must be used as async context manager")
        try:
            resp = await self._http.get(HHDC_BASE)
            resp.raise_for_status()
            html = resp.text
            # Look for .xlsx links in the page
            import re
            xlsx_links = re.findall(r'href="([^"]*\.xlsx)"', html, re.IGNORECASE)
            if xlsx_links:
                link = xlsx_links[0]
                if link.startswith("http"):
                    return link
                return f"https://www.newyorkfed.org{link}"
        except Exception as exc:
            log.warning("nyfed_hhdc.find_xlsx.error", error=str(exc))
        return None

    @retry(
        wait=wait_exponential(multiplier=1, min=2, max=60),
        stop=stop_after_attempt(3),
        retry=retry_if_exception_type((httpx.HTTPError, httpx.TimeoutException)),
        reraise=True,
    )
    async def download_xlsx(self, url: str) -> bytes:
        """Download the HHDC XLSX file."""
        if self._http is None:
            raise RuntimeError("Must be used as async context manager")
        resp = await self._http.get(url)
        resp.raise_for_status()
        return resp.content

    def parse_transition_rates(self, xlsx_bytes: bytes) -> list[dict[str, Any]]:
        """
        Parse the HHDC XLSX to extract credit card transition-to-serious-delinquency rates.

        The HHDC XLSX contains multiple sheets. The relevant sheet is typically
        'Page 12 Data' or 'Transition into Delinquency'. The credit card
        90+ day transition rate is the key series.

        Returns [{date: "YYYY-MM-DD" (quarter start), value: float}].
        """
        try:
            import pandas as pd
            xl = pd.ExcelFile(io.BytesIO(xlsx_bytes))
        except ImportError:
            log.error("nyfed_hhdc.parse.error", error="pandas required for XLSX parsing")
            return []
        except Exception as exc:
            log.error("nyfed_hhdc.parse.error", error=str(exc))
            return []

        results = []
        # Try multiple possible sheet names for transition rates
        target_sheets = [
            "Page 12 Data", "Transition into Delinquency", "Trans into Delinq",
            "Figure 12", "TransitionDelinq",
        ]
        for sheet_name in xl.sheet_names:
            if any(t.lower() in sheet_name.lower() for t in target_sheets):
                try:
                    df = pd.read_excel(io.BytesIO(xlsx_bytes), sheet_name=sheet_name, header=None)
                    # Parse the sheet — structure varies by release
                    # Look for rows containing 'Credit Card' and quarter dates
                    results = _parse_hhdc_sheet(df)
                    if results:
                        break
                except Exception as exc:
                    log.warning("nyfed_hhdc.parse.sheet_error", sheet=sheet_name, error=str(exc))

        return sorted(results, key=lambda x: x["date"])


def _parse_hhdc_sheet(df: "pd.DataFrame") -> list[dict[str, Any]]:
    """
    Parse a HHDC transition rate sheet into [{date, value}] rows.

    The sheet typically has:
      Row 0: "Year:Quarter" headers like "2003:Q1", "2003:Q2", ...
      Subsequent rows: debt type labels + values
    """
    try:
        import pandas as pd
        import re
    except ImportError:
        return []

    results = []
    # Find the header row containing quarter identifiers
    header_row_idx = None
    for i, row in df.iterrows():
        row_str = " ".join(str(v) for v in row.values if pd.notna(v))
        if re.search(r"\d{4}:Q\d", row_str) or re.search(r"Q\d\s+\d{4}", row_str):
            header_row_idx = i
            break

    if header_row_idx is None:
        return results

    # Extract quarter date strings from header row
    header = df.iloc[header_row_idx]
    date_cols: dict[int, str] = {}
    for col_idx, val in enumerate(header):
        val_str = str(val) if pd.notna(val) else ""
        # Match patterns like "2024:Q3" or "Q3 2024"
        m = re.search(r"(\d{4}):Q(\d)", val_str) or re.search(r"Q(\d)\s+(\d{4})", val_str)
        if m:
            if ":" in val_str:
                year, quarter = m.group(1), m.group(2)
            else:
                quarter, year = m.group(1), m.group(2)
            month = (int(quarter) - 1) * 3 + 1
            date_cols[col_idx] = f"{year}-{month:02d}-01"

    if not date_cols:
        return results

    # Find the "Credit Card" row
    for i in range(header_row_idx + 1, min(header_row_idx + 20, len(df))):
        row = df.iloc[i]
        label = str(row.iloc[0]) if pd.notna(row.iloc[0]) else ""
        if "credit card" in label.lower():
            for col_idx, date_str in date_cols.items():
                try:
                    value = float(row.iloc[col_idx])
                    results.append({"date": date_str, "value": value})
                except (ValueError, TypeError, IndexError):
                    pass
            break

    return results


async def ingest_transition_rates(client: "libsql_client.Client") -> int:
    """Download and ingest NY Fed HHDC serious delinquency transition rates."""
    from ingestion.db import get_all_indicators, upsert_observations

    today = datetime.date.today().isoformat()
    all_indicators = await get_all_indicators(client)
    indicator_id = next(
        (ind["id"] for ind in all_indicators if ind["series_id"] == "NYFED_HHDC_CC_SERIOUS_DELINQ"),
        None,
    )
    if indicator_id is None:
        log.warning("nyfed_hhdc.ingest.skip", reason="NYFED_HHDC_CC_SERIOUS_DELINQ not in indicators")
        return 0

    async with NYFedHHDCScraper() as scraper:
        xlsx_url = await scraper.find_current_xlsx_url()
        if not xlsx_url:
            log.warning("nyfed_hhdc.ingest.no_url")
            return 0

        try:
            xlsx_bytes = await scraper.download_xlsx(xlsx_url)
            parsed = scraper.parse_transition_rates(xlsx_bytes)
        except Exception as exc:
            log.error("nyfed_hhdc.ingest.error", error=str(exc))
            return 0

    if not parsed:
        log.warning("nyfed_hhdc.ingest.no_data")
        return 0

    rows = [(r["date"], r["value"]) for r in parsed]
    n = await upsert_observations(client, indicator_id, rows, vintage_date=today)
    log.info("nyfed_hhdc.ingest.ok", rows=n)
    return n
