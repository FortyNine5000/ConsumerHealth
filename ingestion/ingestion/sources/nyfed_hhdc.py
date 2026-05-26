"""
NY Fed Household Debt and Credit (HHDC) data source.

Published quarterly (~3 months after quarter end):
  Q1 → May, Q2 → August, Q3 → November, Q4 → February

Data: credit card and student-loan transition into serious delinquency (90+ days past due).
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

HHDC_SERIES_LABELS: dict[str, tuple[str, ...]] = {
    "NYFED_HHDC_CC_SERIOUS_DELINQ": ("credit card",),
    "NYFED_HHDC_STUDENT_LOAN_SERIOUS_DELINQ": ("student loan", "student loans"),
}


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

    def parse_transition_rates(self, xlsx_bytes: bytes) -> dict[str, list[dict[str, Any]]]:
        """
        Parse the HHDC XLSX to extract transition-to-serious-delinquency rates.

        The HHDC XLSX contains multiple sheets. The relevant sheet is typically
        'Page 12 Data' or 'Transition into Delinquency'. We capture both the
        broad credit-card stress series and the student-loan stress series.

        Returns {series_id: [{date: "YYYY-MM-DD" (quarter start), value: float}]}.
        """
        try:
            import pandas as pd
            xl = pd.ExcelFile(io.BytesIO(xlsx_bytes))
        except ImportError:
            log.error("nyfed_hhdc.parse.error", error="pandas required for XLSX parsing")
            return {series_id: [] for series_id in HHDC_SERIES_LABELS}
        except Exception as exc:
            log.error("nyfed_hhdc.parse.error", error=str(exc))
            return {series_id: [] for series_id in HHDC_SERIES_LABELS}

        results: dict[str, list[dict[str, Any]]] = {series_id: [] for series_id in HHDC_SERIES_LABELS}
        # Try multiple possible sheet names for transition rates
        target_sheets = [
            "Page 12 Data", "Transition into Delinquency", "Trans into Delinq",
            "Figure 12", "TransitionDelinq",
        ]
        candidate_sheets = [
            sheet_name for sheet_name in xl.sheet_names
            if any(t.lower() in sheet_name.lower() for t in target_sheets)
        ] or xl.sheet_names
        for sheet_name in candidate_sheets:
            try:
                df = pd.read_excel(io.BytesIO(xlsx_bytes), sheet_name=sheet_name, header=None)
                # Parse the sheet — structure varies by release.
                # Look for rows containing loan-type labels and quarter dates.
                results = _parse_hhdc_sheet(df)
                if any(results.values()):
                    break
            except Exception as exc:
                log.warning("nyfed_hhdc.parse.sheet_error", sheet=sheet_name, error=str(exc))

        return {
            series_id: sorted(rows, key=lambda x: x["date"])
            for series_id, rows in results.items()
        }


def _parse_hhdc_sheet(df: "pd.DataFrame") -> dict[str, list[dict[str, Any]]]:
    """
    Parse a HHDC transition rate sheet into {series_id: [{date, value}]} rows.

    The sheet typically has:
      Row 0: "Year:Quarter" headers like "2003:Q1", "2003:Q2", ...
      Subsequent rows: debt type labels + values
    """
    try:
        import pandas as pd
        import re
    except ImportError:
        return {series_id: [] for series_id in HHDC_SERIES_LABELS}

    results: dict[str, list[dict[str, Any]]] = {series_id: [] for series_id in HHDC_SERIES_LABELS}
    # Find the header row containing quarter identifiers
    header_row_idx = None
    for i, row in df.iterrows():
        row_str = " ".join(str(v) for v in row.values if pd.notna(v))
        if (
            re.search(r"\d{2,4}:Q[1-4]", row_str)
            or re.search(r"Q[1-4]\s+\d{2,4}", row_str)
            or re.search(r"\d{4}Q[1-4]", row_str)
        ):
            header_row_idx = i
            break

    if header_row_idx is None:
        return results

    # Extract quarter date strings from header row
    header = df.iloc[header_row_idx]
    date_cols: dict[int, str] = {}
    for col_idx, val in enumerate(header):
        val_str = str(val) if pd.notna(val) else ""
        parsed = _quarter_start(val_str)
        if parsed:
            date_cols[col_idx] = parsed

    if not date_cols:
        return results

    # Find the loan-type rows.
    for i in range(header_row_idx + 1, min(header_row_idx + 30, len(df))):
        row = df.iloc[i]
        label = " ".join(str(v) for v in row.iloc[:4].values if pd.notna(v)).lower()
        for series_id, label_terms in HHDC_SERIES_LABELS.items():
            if results[series_id] or not any(term in label for term in label_terms):
                continue
            for col_idx, date_str in date_cols.items():
                try:
                    value = float(row.iloc[col_idx])
                    results[series_id].append({"date": date_str, "value": value})
                except (ValueError, TypeError, IndexError):
                    pass

    return results


def _quarter_start(value: str) -> str | None:
    """Parse HHDC quarter labels such as 2025:Q1, 25:Q1, Q1 2025, or 2025Q1."""
    import re

    text = value.strip()
    patterns = [
        (r"(\d{2,4}):Q([1-4])", "year_first"),
        (r"Q([1-4])\s+(\d{2,4})", "quarter_first"),
        (r"(\d{4})Q([1-4])", "year_first"),
    ]
    for pattern, order in patterns:
        m = re.search(pattern, text)
        if not m:
            continue
        if order == "year_first":
            year_raw, quarter_raw = m.group(1), m.group(2)
        else:
            quarter_raw, year_raw = m.group(1), m.group(2)
        year = int(year_raw)
        if year < 100:
            year += 2000 if year < 70 else 1900
        month = (int(quarter_raw) - 1) * 3 + 1
        return f"{year}-{month:02d}-01"
    return None


async def ingest_transition_rates(client: "libsql_client.Client") -> int:
    """Download and ingest NY Fed HHDC serious delinquency transition rates."""
    from ingestion.db import get_all_indicators, upsert_observations

    today = datetime.date.today().isoformat()
    all_indicators = await get_all_indicators(client)
    series_to_indicator = {
        ind["series_id"]: ind["id"]
        for ind in all_indicators
        if ind["series_id"] in HHDC_SERIES_LABELS
    }
    if not series_to_indicator:
        log.warning("nyfed_hhdc.ingest.skip", reason="HHDC transition indicators not in indicators")
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

    if not any(parsed.values()):
        log.warning("nyfed_hhdc.ingest.no_data")
        return 0

    total_rows = 0
    for series_id, parsed_rows in parsed.items():
        indicator_id = series_to_indicator.get(series_id)
        if indicator_id is None or not parsed_rows:
            continue
        rows = [(r["date"], r["value"]) for r in parsed_rows]
        n = await upsert_observations(client, indicator_id, rows, vintage_date=today)
        total_rows += n
        log.info("nyfed_hhdc.ingest.series.ok", series_id=series_id, rows=n)

    log.info("nyfed_hhdc.ingest.ok", rows=total_rows)
    return total_rows
