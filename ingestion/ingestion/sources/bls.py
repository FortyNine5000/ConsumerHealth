"""
Bureau of Labor Statistics (BLS) Public Data API v2.

Used for BLS series that either:
  (a) are not available on FRED, or
  (b) we want to pull directly from BLS for auditability.

API docs: https://www.bls.gov/developers/api_signature_v2.htm
Limits:
  - 500 requests/day (registered key)
  - 50 series per request
  - 20 years of history per request
  - Data available from series' first publication through current date

BLS date format: year=YYYY, period=M01–M12 (monthly) or Q01–Q04 (quarterly).
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

BLS_ENDPOINT = "https://api.bls.gov/publicAPI/v2/timeseries/data/"

# BLS series IDs for supplementary labor market data not available via FRED.
# Most core series (UNRATE, PAYEMS, etc.) are fetched via FRED to avoid BLS daily limits.
BLS_SUPPLEMENTARY: list[str] = [
    # CPS (Current Population Survey) — labor market
    "LNS14000000",          # Unemployment Rate (BLS direct; mirrors FRED UNRATE)
    "LNS11300000",          # Labor Force Participation Rate
    "LNS13327709",          # U-6 Total Unemployed
    "LNS12300000",          # Employment-Population Ratio
    # CES (Current Employment Statistics) — payrolls
    "CES0500000002",        # Average Weekly Hours, Private
    "CES0500000003",        # Average Hourly Earnings, Private
    "CES0500000013",        # Real Average Hourly Earnings, Private
    "CES6056132001",        # Temporary Help Services Employment
    # JOLTS
    "JTS1000000000000000QUR",  # Quits Rate, Total Private (JOLTS)
]


def _bls_period_to_date(year: str, period: str) -> str | None:
    """
    Convert BLS year/period to ISO-8601 date string (first day of period).

    Monthly: M01 → YYYY-01-01, M13 = annual average → skip
    Quarterly: Q01 → YYYY-01-01, Q02 → YYYY-04-01, etc.
    """
    if period.startswith("M"):
        month_num = int(period[1:])
        if month_num > 12:
            return None  # M13 = annual average; skip
        return f"{year}-{month_num:02d}-01"
    elif period.startswith("Q"):
        quarter_num = int(period[1:])
        month = (quarter_num - 1) * 3 + 1
        return f"{year}-{month:02d}-01"
    return None


class BLSClient:
    """Async HTTP client for BLS Public Data API v2."""

    def __init__(self, api_key: str) -> None:
        self.api_key = api_key
        self._http: httpx.AsyncClient | None = None

    async def __aenter__(self) -> "BLSClient":
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
    async def fetch_batch(
        self,
        series_ids: list[str],
        start_year: int,
        end_year: int,
        catalog: bool = False,
    ) -> dict[str, list[dict[str, Any]]]:
        """
        Fetch a batch of BLS series for a date range.

        Returns {series_id: [{date: "YYYY-MM-DD", value: float | None}]}.
        BLS v2 allows ≤50 series and ≤20 years per request.
        """
        if self._http is None:
            raise RuntimeError("BLSClient must be used as async context manager")
        if len(series_ids) > settings.bls_max_series_per_request:
            raise ValueError(
                f"BLS allows max {settings.bls_max_series_per_request} series per request"
            )

        payload: dict[str, Any] = {
            "seriesid": series_ids,
            "startyear": str(start_year),
            "endyear": str(end_year),
            "registrationkey": self.api_key,
        }
        if catalog:
            payload["catalog"] = True

        log.debug("bls.fetch", series_count=len(series_ids), years=f"{start_year}-{end_year}")
        resp = await self._http.post(BLS_ENDPOINT, json=payload)
        resp.raise_for_status()
        data = resp.json()

        if data.get("status") != "REQUEST_SUCCEEDED":
            messages = data.get("message", [])
            log.warning("bls.api.warning", messages=messages)

        results: dict[str, list[dict[str, Any]]] = {}
        for series in data.get("Results", {}).get("series", []):
            sid = series["seriesID"]
            obs_list = []
            for obs in series.get("data", []):
                date = _bls_period_to_date(obs["year"], obs["period"])
                if date is None:
                    continue
                try:
                    value = float(obs["value"])
                except (ValueError, TypeError):
                    value = None
                obs_list.append({"date": date, "value": value})
            # BLS returns newest-first; sort to chronological
            obs_list.sort(key=lambda x: x["date"])
            results[sid] = obs_list

        return results

    async def fetch_full_history(
        self,
        series_ids: list[str],
        start_year: int = 1990,
    ) -> dict[str, list[dict[str, Any]]]:
        """
        Fetch full history for a list of series, chunking by 20-year windows.
        BLS allows 20 years per request; 1990-present needs ceil(35/20)=2 chunks.
        """
        current_year = datetime.date.today().year
        all_results: dict[str, list[dict[str, Any]]] = {sid: [] for sid in series_ids}

        # Chunk into 20-year windows
        chunk_start = start_year
        while chunk_start <= current_year:
            chunk_end = min(chunk_start + 19, current_year)
            # Batch series in groups of max 50
            for i in range(0, len(series_ids), settings.bls_max_series_per_request):
                batch = series_ids[i : i + settings.bls_max_series_per_request]
                try:
                    chunk_results = await self.fetch_batch(batch, chunk_start, chunk_end)
                    for sid, obs in chunk_results.items():
                        all_results[sid].extend(obs)
                except Exception as exc:
                    log.error(
                        "bls.fetch.error",
                        batch=batch,
                        years=f"{chunk_start}-{chunk_end}",
                        error=str(exc),
                    )
            chunk_start += 20
            await asyncio.sleep(1.0)  # BLS is less generous than FRED on rate limits

        # Deduplicate and sort
        for sid in all_results:
            seen: set[str] = set()
            deduped = []
            for obs in sorted(all_results[sid], key=lambda x: x["date"]):
                if obs["date"] not in seen:
                    seen.add(obs["date"])
                    deduped.append(obs)
            all_results[sid] = deduped

        return all_results


async def ingest_supplementary(
    client: "libsql_client.Client",
    start_year: int = 1990,
    series_ids: list[str] | None = None,
) -> int:
    """Ingest supplementary BLS series into indicator_observations."""
    from ingestion.db import get_all_indicators, upsert_observations

    ids_to_fetch = series_ids or BLS_SUPPLEMENTARY
    today = datetime.date.today().isoformat()

    all_indicators = await get_all_indicators(client)
    series_to_ind: dict[str, int] = {
        ind["series_id"]: ind["id"]
        for ind in all_indicators
        if ind["source"] == "bls"
    }

    total_rows = 0
    async with BLSClient(api_key=settings.bls_api_key) as bls:
        history = await bls.fetch_full_history(ids_to_fetch, start_year=start_year)
        for series_id, observations in history.items():
            if series_id not in series_to_ind:
                log.debug("bls.ingest.skip", series_id=series_id, reason="not in indicators table")
                continue
            indicator_id = series_to_ind[series_id]
            rows = [(obs["date"], obs["value"]) for obs in observations]
            n = await upsert_observations(client, indicator_id, rows, vintage_date=today)
            total_rows += n
            log.info("bls.ingest.ok", series_id=series_id, rows=n)

    return total_rows
