"""
Energy Information Administration (EIA) Open Data API v2.

Fetches U.S. retail regular gasoline prices (weekly, Monday ~5pm ET).

API docs: https://www.eia.gov/opendata/documentation.php
Series: PET.EMM_EPMR_PTE_NUS_DPG.W (U.S. Regular Retail Gasoline, $/gallon, weekly)
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

EIA_ENDPOINT = "https://api.eia.gov/v2/petroleum/pri/gnd/data/"


class EIAClient:
    """Async HTTP client for the EIA Open Data API v2."""

    def __init__(self, api_key: str) -> None:
        self.api_key = api_key
        self._http: httpx.AsyncClient | None = None

    async def __aenter__(self) -> "EIAClient":
        self._http = httpx.AsyncClient(timeout=30.0)
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
    async def fetch_gas_prices(
        self,
        start: str = "1990-01-01",
        end: str | None = None,
    ) -> list[dict[str, Any]]:
        """
        Fetch weekly U.S. regular retail gasoline prices.
        Returns [{date: "YYYY-MM-DD", value: float | None}].
        """
        if self._http is None:
            raise RuntimeError("EIAClient must be used as async context manager")

        params: dict[str, Any] = {
            "api_key": self.api_key,
            "frequency": "weekly",
            "data[0]": "value",
            "facets[product][]": "EPMR",   # Regular grade
            "facets[area][]": "NUS",        # National US
            "start": start,
            "sort[0][column]": "period",
            "sort[0][direction]": "asc",
            "offset": 0,
            "length": 5000,
        }
        if end:
            params["end"] = end

        log.debug("eia.fetch_gas_prices", start=start)
        resp = await self._http.get(EIA_ENDPOINT, params=params)
        resp.raise_for_status()
        data = resp.json()

        observations = []
        for row in data.get("response", {}).get("data", []):
            period = row.get("period", "")
            # EIA weekly periods are in YYYY-MM-DD format already
            date = period if len(period) == 10 else None
            if not date:
                continue
            try:
                value = float(row["value"])
            except (ValueError, TypeError, KeyError):
                value = None
            observations.append({"date": date, "value": value})

        return sorted(observations, key=lambda x: x["date"])


async def ingest_gas_prices(client: "libsql_client.Client") -> int:
    """Fetch EIA weekly gas prices and upsert to indicator_observations."""
    from ingestion.db import get_all_indicators, upsert_observations

    today = datetime.date.today().isoformat()
    all_indicators = await get_all_indicators(client)
    indicator_id = next(
        (ind["id"] for ind in all_indicators if ind["series_id"] == "EIA_GAS_US_REGULAR"),
        None,
    )
    if indicator_id is None:
        log.warning("eia.ingest.skip", reason="EIA_GAS_US_REGULAR not in indicators table")
        return 0

    async with EIAClient(api_key=settings.eia_api_key) as eia:
        try:
            observations = await eia.fetch_gas_prices(start="1990-01-01")
        except Exception as exc:
            log.error("eia.fetch.error", error=str(exc))
            return 0

    rows = [(obs["date"], obs["value"]) for obs in observations]
    n = await upsert_observations(client, indicator_id, rows, vintage_date=today)
    log.info("eia.ingest.ok", rows=n)
    return n
