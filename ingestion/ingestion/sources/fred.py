"""
FRED (Federal Reserve Bank of St. Louis) data source.

Fetches all scored and supporting indicator series from the FRED REST API.
Rate limit: 120 requests/minute per FRED support.
We sleep 0.5s between calls → ~2 req/sec → well within limits.

API docs: https://fred.stlouisfed.org/docs/api/fred/series_observations.html
"""

from __future__ import annotations

import asyncio
import datetime
import time
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

FRED_BASE = "https://api.stlouisfed.org/fred/series/observations"

# All FRED series IDs to fetch (scored + supporting).
# Excludes non-FRED sources: TSA, Manheim, NY Fed HHDC, NY Fed SCE, EIA, BEA-specific.
FRED_ALL_SERIES: list[str] = [
    # ── Labor & Income (scored) ──────────────────────────────────────────────
    "UNRATE",           # Unemployment Rate
    "PAYEMS",           # Nonfarm Payrolls (raw level; transform to 3mo avg change)
    "IC4WSA",           # Initial Claims 4-week avg
    "CCSA",             # Continued Claims
    "CES0500000013",    # Real Average Hourly Earnings, Private Employees
    # ── Labor (supporting) ───────────────────────────────────────────────────
    "JTSJOL",           # JOLTS Job Openings
    "JTS1000QUR",       # JOLTS Quits Rate Total Private
    "TEMPHELPS",        # Temporary Help Employment
    "CIVPART",          # Labor Force Participation Rate
    "U6RATE",           # U-6 Total Unemployed + Underemployed
    "AWHAETP",          # Average Weekly Hours, Private
    "EMRATIO",          # Employment-Population Ratio
    # ── Household Balance Sheet (scored) ────────────────────────────────────
    "PSAVERT",          # Personal Saving Rate
    "DSPIC96",          # Real Disposable Personal Income
    "DSPI",             # Nominal DPI (supporting: net worth denominator)
    "TDSP",             # Household Debt Service Ratio
    "BOGZ1FL192090005Q",# Household Net Worth (Z.1, quarterly)
    # ── Credit Stress (scored) ───────────────────────────────────────────────
    "DRCCLACBS",        # Credit Card Delinquency Rate, All Banks
    "DRCLACBS",         # Consumer Loan Delinquency Rate, All Banks
    "CORCCACBS",        # Credit Card Charge-Off Rate, All Banks
    "DRTSCLCC",         # SLOOS Net Tightening Consumer Credit Card
    # ── Credit (supporting) ──────────────────────────────────────────────────
    "DRCCLOBS",         # CC Delinquency, Banks NOT in Top 100 (subprime signal)
    "STLFSI4",          # St. Louis Financial Stress Index
    "T10Y3M",           # Yield Curve 10Y-3M
    "SAHMCURRENT",      # Sahm Rule Recession Indicator
    # ── Spending & Demand (scored) ───────────────────────────────────────────
    "PCEC96",           # Real PCE (chained dollars)
    "RRSFS",            # Advance Real Retail & Food Services Sales
    # ── Sentiment (scored) ──────────────────────────────────────────────────
    "UMCSENT",          # UMich Consumer Sentiment
    "CSCICP03USM665S",  # OECD Consumer Confidence (Conference Board proxy)
    # ── Inflation (scored) ──────────────────────────────────────────────────
    "CPIAUCSL",         # Headline CPI
    "CPILFESL",         # Core CPI
    "CUSR0000SAH1",     # Shelter CPI
    # ── Big-Ticket Affordability (scored) ────────────────────────────────────
    "MORTGAGE30US",     # 30Y Fixed Mortgage Rate (Freddie Mac PMMS via FRED)
    "RIFLPBCIANM72NM",  # New Auto Loan Rate 72-month
    "TERMCBCCALLNS",    # Credit Card Interest Rate
    # ── Housing Affordability components (supporting) ─────────────────────────
    "MSPUS",            # Median Home Sales Price (quarterly)
    "MEHOINUSA672N",    # Median Household Income (annual Census via FRED)
]


class FREDClient:
    """Async HTTP client for the FRED REST API."""

    def __init__(
        self,
        api_key: str,
        sleep_seconds: float = 0.5,
    ) -> None:
        self.api_key = api_key
        self.sleep_seconds = sleep_seconds
        self._http: httpx.AsyncClient | None = None

    async def __aenter__(self) -> "FREDClient":
        self._http = httpx.AsyncClient(timeout=30.0)
        return self

    async def __aexit__(self, *args: Any) -> None:
        if self._http:
            await self._http.aclose()

    @retry(
        wait=wait_exponential(multiplier=1, min=2, max=60),
        stop=stop_after_attempt(5),
        retry=retry_if_exception_type((httpx.HTTPError, httpx.TimeoutException)),
        reraise=True,
    )
    async def fetch_series(
        self,
        series_id: str,
        observation_start: str = "1985-01-01",
        observation_end: str | None = None,
        units: str = "lin",
        frequency: str | None = None,
    ) -> list[dict[str, Any]]:
        """
        Fetch all observations for a FRED series.

        Returns list of {"date": "YYYY-MM-DD", "value": float | None}.
        FRED returns "." for missing values — converted to None here.
        """
        if self._http is None:
            raise RuntimeError("FREDClient must be used as async context manager")

        params: dict[str, Any] = {
            "series_id": series_id,
            "api_key": self.api_key,
            "file_type": "json",
            "observation_start": observation_start,
            "units": units,
        }
        if observation_end:
            params["observation_end"] = observation_end
        if frequency:
            params["frequency"] = frequency

        log.debug("fred.fetch", series_id=series_id)
        resp = await self._http.get(FRED_BASE, params=params)
        resp.raise_for_status()
        data = resp.json()

        observations = data.get("observations", [])
        return [
            {
                "date": obs["date"],
                "value": None if obs["value"] == "." else float(obs["value"]),
            }
            for obs in observations
        ]

    async def fetch_all(
        self,
        series_ids: list[str],
        observation_start: str = "1985-01-01",
    ) -> dict[str, list[dict[str, Any]]]:
        """Fetch multiple series sequentially with rate-limit sleep."""
        results: dict[str, list[dict[str, Any]]] = {}
        for series_id in series_ids:
            results[series_id] = await self.fetch_series(
                series_id, observation_start=observation_start
            )
            await asyncio.sleep(self.sleep_seconds)
        return results


async def ingest_all(
    client: "libsql_client.Client",
    observation_start: str = "1985-01-01",
    series_ids: list[str] | None = None,
) -> int:
    """
    Fetch all FRED series and upsert raw observations into indicator_observations.

    Returns total rows upserted.
    """
    from ingestion.db import get_all_indicators, upsert_observations

    ids_to_fetch = series_ids or FRED_ALL_SERIES
    today = datetime.date.today().isoformat()

    # Build series_id → indicator_id mapping
    all_indicators = await get_all_indicators(client)
    series_to_ind: dict[str, int] = {
        ind["series_id"]: ind["id"]
        for ind in all_indicators
        if ind["source"] == "fred"
    }

    total_rows = 0
    async with FREDClient(
        api_key=settings.fred_api_key,
        sleep_seconds=settings.fred_sleep_seconds,
    ) as fred:
        for series_id in ids_to_fetch:
            if series_id not in series_to_ind:
                log.warning("fred.ingest.skip", series_id=series_id, reason="not in indicators table")
                continue

            indicator_id = series_to_ind[series_id]
            try:
                observations = await fred.fetch_series(
                    series_id, observation_start=observation_start
                )
            except Exception as exc:
                log.error("fred.fetch.error", series_id=series_id, error=str(exc))
                continue

            rows = [(obs["date"], obs["value"]) for obs in observations]
            n = await upsert_observations(client, indicator_id, rows, vintage_date=today)
            total_rows += n
            log.info("fred.ingest.ok", series_id=series_id, rows=n)

    return total_rows
