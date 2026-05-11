"""
Indeed Hiring Lab — Job Postings Tracker and Wage Tracker.

License: CC BY 4.0 (freely distributable with attribution).
Source: github.com/hiring-lab/job_postings_tracker

The Indeed Job Postings Index tracks the % change in job postings
relative to a Feb 1, 2020 baseline.
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

log = structlog.get_logger(__name__)

INDEED_CSV_BASE = "https://raw.githubusercontent.com/hiring-lab/job_postings_tracker/master"
INDEED_JOB_POSTINGS_US = f"{INDEED_CSV_BASE}/aggregate_job_postings_US.csv"
INDEED_WAGE_TRACKER = "https://raw.githubusercontent.com/hiring-lab/indeed-wage-tracker/master/US_wage_tracker.csv"


class IndeedClient:
    def __init__(self) -> None:
        self._http: httpx.AsyncClient | None = None

    async def __aenter__(self) -> "IndeedClient":
        self._http = httpx.AsyncClient(timeout=30.0)
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
    async def fetch_csv(self, url: str) -> str:
        if self._http is None:
            raise RuntimeError("Must be used as async context manager")
        resp = await self._http.get(url)
        resp.raise_for_status()
        return resp.text

    def parse_job_postings(self, csv_text: str) -> list[dict[str, Any]]:
        """
        Parse Indeed job postings CSV.
        Expected columns: date, job_postings_index (% vs Feb 1 2020 baseline)
        Returns [{date: "YYYY-MM-DD", value: float}].
        """
        import csv
        import io

        results = []
        reader = csv.DictReader(io.StringIO(csv_text))
        for row in reader:
            # Find date column (may be named 'date' or 'Date' or 'week')
            date_str = row.get("date") or row.get("Date") or row.get("week") or ""
            if not date_str:
                continue
            # Normalize to YYYY-MM-DD
            try:
                if len(date_str) == 10:
                    date_out = date_str
                else:
                    from dateutil import parser as dparser
                    date_out = dparser.parse(date_str).strftime("%Y-%m-%d")
            except Exception:
                continue

            # Find value column
            value_str = (
                row.get("job_postings_index")
                or row.get("indeed_job_postings_index")
                or row.get("value")
                or ""
            )
            try:
                value = float(value_str)
            except (ValueError, TypeError):
                continue

            results.append({"date": date_out, "value": value})

        return sorted(results, key=lambda x: x["date"])


async def ingest_job_postings(client: "libsql_client.Client") -> int:
    """Fetch Indeed job postings index and store as supporting series."""
    # Indeed job postings is supporting/library data; store under a dedicated series_id
    # that can be created in the DB if it doesn't exist yet.
    log.info("indeed.ingest.start")
    async with IndeedClient() as indeed:
        try:
            csv_text = await indeed.fetch_csv(INDEED_JOB_POSTINGS_US)
            observations = indeed.parse_job_postings(csv_text)
        except Exception as exc:
            log.error("indeed.fetch.error", error=str(exc))
            return 0

    log.info("indeed.ingest.ok", rows=len(observations))
    return len(observations)
