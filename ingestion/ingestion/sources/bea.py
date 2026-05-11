"""
Bureau of Economic Analysis (BEA) NIPA data source.

Fetches Personal Income and Outlays (NIPA Table T20600 / T24200U) monthly data.
Key series extracted:
  - Personal Saving Rate (cross-check with FRED PSAVERT)
  - Real PCE subcategories (Food Services & Accommodations)
  - Nominal Disposable Personal Income

API docs: https://apps.bea.gov/api/data/
Rate limit: undocumented but generous (~1000 req/day assumed).
"""

from __future__ import annotations

import asyncio
import datetime
from typing import Any

import httpx
import structlog
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from ingestion.config import settings

log = structlog.get_logger(__name__)

BEA_ENDPOINT = "https://apps.bea.gov/api/data"

# NIPA tables to fetch:
# T20600 = Personal Income and Its Disposition (Monthly)
# T24200U = Personal Consumption Expenditures by Type of Product (Monthly, chained $)
NIPA_TABLES = [
    ("T20600", "M"),   # Personal Income & Outlays (monthly)
    ("T24200U", "M"),  # Real PCE by category (monthly, chained)
]

# BEA line descriptions we care about (substring match)
# These map to indicator series_ids we created in seed_indicators.py
BEA_LINE_MAP: dict[str, str] = {
    "Personal saving as a percentage": "PSAVERT",           # saving rate
    "Food services and accommodations": "DFXARC1Q027SBEA",  # real PCE food services
    "Disposable personal income": "DSPIC96",                # DPI (nominal or real depending on table)
}


def _bea_period_to_date(year: str, period: str) -> str | None:
    """
    Convert BEA TimePeriod string (e.g. '2024M01', '2024Q1') to ISO date.
    """
    period_str = str(period)
    if "M" in period_str:
        # Format: YYYYMM (e.g. 2024M01) or YYYY-MM
        parts = period_str.replace("M", "-")
        try:
            year_p, month_p = parts.split("-")
            return f"{year_p.strip()}-{int(month_p):02d}-01"
        except ValueError:
            return None
    elif "Q" in period_str:
        parts = period_str.split("Q")
        try:
            y, q = parts
            month = (int(q) - 1) * 3 + 1
            return f"{y.strip()}-{month:02d}-01"
        except (ValueError, IndexError):
            return None
    elif "A" in period_str:
        return None  # annual — skip
    return None


class BEAClient:
    """Async HTTP client for BEA NIPA API."""

    def __init__(self, api_key: str) -> None:
        self.api_key = api_key
        self._http: httpx.AsyncClient | None = None

    async def __aenter__(self) -> "BEAClient":
        self._http = httpx.AsyncClient(timeout=60.0)
        return self

    async def __aexit__(self, *args: Any) -> None:
        if self._http:
            await self._http.aclose()

    @retry(
        wait=wait_exponential(multiplier=1, min=2, max=60),
        stop=stop_after_attempt(4),
        retry=retry_if_exception_type((httpx.HTTPError, httpx.TimeoutException)),
        reraise=True,
    )
    async def fetch_nipa_table(
        self,
        table_name: str,
        frequency: str = "M",
        year: str = "ALL",
    ) -> dict[str, Any]:
        """
        Fetch a full NIPA table (all lines, all available years).
        Returns the raw BEA JSON response.
        """
        if self._http is None:
            raise RuntimeError("BEAClient must be used as async context manager")

        params = {
            "UserID": self.api_key,
            "method": "GetData",
            "datasetname": "NIPA",
            "TableName": table_name,
            "Frequency": frequency,
            "Year": year,
            "ResultFormat": "JSON",
        }
        log.debug("bea.fetch", table=table_name, frequency=frequency)
        resp = await self._http.get(BEA_ENDPOINT, params=params)
        resp.raise_for_status()
        return resp.json()

    def parse_nipa_table(
        self,
        response: dict[str, Any],
        target_descriptions: list[str] | None = None,
    ) -> dict[str, list[dict[str, Any]]]:
        """
        Parse BEA NIPA response into {series_label: [{date, value}]}.

        target_descriptions: if provided, only extract rows whose LineDescription
        contains one of these substrings (case-insensitive).
        """
        results: dict[str, list[dict[str, Any]]] = {}

        try:
            data_rows = response["BEAAPI"]["Results"]["Data"]
        except (KeyError, TypeError):
            log.warning("bea.parse.no_data", response_keys=list(response.keys()))
            return results

        for row in data_rows:
            line_desc = row.get("LineDescription", "")
            series_name = row.get("SeriesCode", line_desc)
            time_period = row.get("TimePeriod", "")
            raw_value = row.get("DataValue", "")

            if target_descriptions:
                if not any(t.lower() in line_desc.lower() for t in target_descriptions):
                    continue

            date = _bea_period_to_date("", time_period)
            if date is None:
                continue

            try:
                value = float(str(raw_value).replace(",", ""))
            except (ValueError, TypeError):
                value = None

            key = series_name or line_desc
            if key not in results:
                results[key] = []
            results[key].append({"date": date, "value": value, "line_desc": line_desc})

        # Sort each series chronologically
        for key in results:
            results[key].sort(key=lambda x: x["date"])

        return results


async def ingest_t20600(client: "libsql_client.Client") -> int:
    """
    Fetch NIPA T20600 (Personal Income and Outlays, monthly) and upsert to DB.
    Extracts Food Services & Accommodations PCE component.
    """
    from ingestion.db import get_all_indicators, upsert_observations

    today = datetime.date.today().isoformat()
    all_indicators = await get_all_indicators(client)
    series_to_ind: dict[str, int] = {
        ind["series_id"]: ind["id"]
        for ind in all_indicators
    }

    total_rows = 0
    async with BEAClient(api_key=settings.bea_api_key) as bea:
        # Fetch T20600 — Personal Income & Outlays
        for table_name, frequency in NIPA_TABLES:
            try:
                response = await bea.fetch_nipa_table(table_name, frequency=frequency)
                parsed = bea.parse_nipa_table(
                    response,
                    target_descriptions=list(BEA_LINE_MAP.keys()),
                )
            except Exception as exc:
                log.error("bea.fetch.error", table=table_name, error=str(exc))
                continue

            for line_key, obs_list in parsed.items():
                # Map line key to our series_id
                matched_series_id = None
                for desc_pattern, series_id in BEA_LINE_MAP.items():
                    if desc_pattern.lower() in line_key.lower():
                        matched_series_id = series_id
                        break

                if not matched_series_id:
                    continue
                if matched_series_id not in series_to_ind:
                    log.debug("bea.ingest.skip", series_id=matched_series_id, reason="not in indicators")
                    continue

                indicator_id = series_to_ind[matched_series_id]
                rows = [(obs["date"], obs["value"]) for obs in obs_list]
                n = await upsert_observations(client, indicator_id, rows, vintage_date=today)
                total_rows += n
                log.info("bea.ingest.ok", series_id=matched_series_id, rows=n)

            await asyncio.sleep(0.5)

    return total_rows
