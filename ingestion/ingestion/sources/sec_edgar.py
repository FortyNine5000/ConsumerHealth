"""
SEC EDGAR 8-K scraper for earnings-call quote ingestion.

Checks EDGAR submissions API for new 8-K filings from the v1 watchlist.
Extracts Exhibit 99.x (prepared remarks / press releases — public domain).

Legal basis: SEC content is U.S. government work, not subject to copyright.
Per spec §8.2: always source from 8-K Exhibit 99 first; cap quotes at 150 words.

SEC requires a descriptive User-Agent: set SEC_USER_AGENT in .env.
Rate limit: 10 requests/second per sec.gov/developer.
"""

from __future__ import annotations

import asyncio
import datetime
import json
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

from ingestion.config import settings

log = structlog.get_logger(__name__)

EDGAR_SUBMISSIONS_BASE = "https://data.sec.gov/submissions"
EDGAR_ARCHIVES_BASE = "https://www.sec.gov/Archives/edgar/data"

# V1 watchlist: 32 companies with their SEC CIK numbers
# Format: (ticker, cik_padded_10_digits)
V1_WATCHLIST: list[tuple[str, str]] = [
    # Banks / Card Issuers
    ("JPM",  "0000019617"),
    ("BAC",  "0000070858"),
    ("C",    "0000831001"),
    ("WFC",  "0000072971"),
    ("COF",  "0000927628"),
    ("AXP",  "0000004962"),
    ("SYF",  "0001601712"),
    ("DFS",  "0001393612"),
    ("ALLY", "0000040729"),
    ("BFH",  "0001556727"),
    # Payment Networks
    ("V",    "0001403161"),
    ("MA",   "0001141391"),
    ("PYPL", "0001633917"),
    # Mass Retail / Discount
    ("WMT",  "0000104169"),
    ("TGT",  "0000027419"),
    ("COST", "0000909832"),
    ("DG",   "0000029534"),
    ("DLTR", "0000935703"),
    ("AMZN", "0001018724"),
    # Home Improvement / Specialty
    ("HD",   "0000354950"),
    ("LOW",  "0000060667"),
    # Restaurants / Delivery
    ("MCD",  "0000063908"),
    ("SBUX", "0000829224"),
    ("CMG",  "0001058090"),
    ("DASH", "0001792789"),
    # Autos / Auto Credit
    ("KMX",  "0001170010"),
    ("F",    "0000037996"),
    ("CVNA", "0001690820"),
    # Housing / Builders
    ("LEN",  "0000060667"),  # placeholder — verify CIK
    ("DHI",  "0000045012"),
    # Travel / Lodging
    ("DAL",  "0000027904"),
    ("MAR",  "0001048268"),
]


class EDGARClient:
    """Async HTTP client for SEC EDGAR APIs."""

    SLEEP_BETWEEN_REQUESTS = 0.11  # ~9 req/sec (under 10/sec limit)

    def __init__(self, user_agent: str) -> None:
        self.user_agent = user_agent
        self._http: httpx.AsyncClient | None = None

    async def __aenter__(self) -> "EDGARClient":
        self._http = httpx.AsyncClient(
            timeout=30.0,
            headers={"User-Agent": self.user_agent},
        )
        return self

    async def __aexit__(self, *args: Any) -> None:
        if self._http:
            await self._http.aclose()

    @retry(
        wait=wait_exponential(multiplier=1, min=2, max=30),
        stop=stop_after_attempt(4),
        retry=retry_if_exception_type((httpx.HTTPError, httpx.TimeoutException)),
        reraise=True,
    )
    async def get_submissions(self, cik: str) -> dict[str, Any]:
        """Fetch the submissions JSON for a CIK (contains recent filing list)."""
        if self._http is None:
            raise RuntimeError("Must be used as async context manager")
        url = f"{EDGAR_SUBMISSIONS_BASE}/CIK{cik}.json"
        resp = await self._http.get(url)
        resp.raise_for_status()
        await asyncio.sleep(self.SLEEP_BETWEEN_REQUESTS)
        return resp.json()

    def get_recent_8k_accessions(
        self,
        submissions: dict[str, Any],
        since_date: str | None = None,
        max_results: int = 5,
    ) -> list[dict[str, Any]]:
        """
        Extract recent 8-K filing accession numbers from submissions JSON.

        Returns list of {accession: str, filed_date: str, primary_document: str}.
        """
        filings = submissions.get("filings", {}).get("recent", {})
        forms = filings.get("form", [])
        dates = filings.get("filingDate", [])
        accessions = filings.get("accessionNumber", [])
        primary_docs = filings.get("primaryDocument", [])

        results = []
        for form, date, accession, primary_doc in zip(forms, dates, accessions, primary_docs):
            if form not in ("8-K", "8-K/A"):
                continue
            if since_date and date < since_date:
                continue
            results.append({
                "accession": accession.replace("-", ""),
                "accession_formatted": accession,
                "filed_date": date,
                "primary_document": primary_doc,
            })
            if len(results) >= max_results:
                break

        return results

    @retry(
        wait=wait_exponential(multiplier=1, min=2, max=30),
        stop=stop_after_attempt(3),
        retry=retry_if_exception_type((httpx.HTTPError, httpx.TimeoutException)),
        reraise=True,
    )
    async def get_filing_index(self, cik: str, accession: str) -> dict[str, Any]:
        """Fetch the filing index JSON to find Exhibit 99.x documents."""
        if self._http is None:
            raise RuntimeError("Must be used as async context manager")
        url = f"{EDGAR_ARCHIVES_BASE}/{cik.lstrip('0')}/{accession}/{accession}-index.json"
        resp = await self._http.get(url)
        resp.raise_for_status()
        await asyncio.sleep(self.SLEEP_BETWEEN_REQUESTS)
        return resp.json()

    def find_exhibit_99_docs(self, index: dict[str, Any]) -> list[dict[str, Any]]:
        """
        Find Exhibit 99.x documents in a filing index.
        Returns list of {name: str, type: str, url: str}.
        """
        docs = index.get("documents", [])
        exhibits = []
        for doc in docs:
            doc_type = doc.get("type", "")
            if doc_type.startswith("EX-99") or "99." in doc_type:
                exhibits.append({
                    "name": doc.get("name", ""),
                    "type": doc_type,
                    "url": doc.get("documentUrl", ""),
                })
        return exhibits

    @retry(
        wait=wait_exponential(multiplier=1, min=2, max=30),
        stop=stop_after_attempt(3),
        retry=retry_if_exception_type((httpx.HTTPError, httpx.TimeoutException)),
        reraise=True,
    )
    async def fetch_exhibit_text(self, url: str) -> str:
        """Download and return the plain text of an Exhibit 99 document."""
        if self._http is None:
            raise RuntimeError("Must be used as async context manager")
        resp = await self._http.get(url)
        resp.raise_for_status()
        await asyncio.sleep(self.SLEEP_BETWEEN_REQUESTS)
        # Strip HTML tags if present
        content = resp.text
        if "<html" in content.lower() or "<body" in content.lower():
            content = re.sub(r"<[^>]+>", " ", content)
            content = re.sub(r"\s+", " ", content).strip()
        return content


async def check_new_filings(
    client: "libsql_client.Client",
    since_days: int = 30,
) -> list[dict[str, Any]]:
    """
    Check EDGAR for new 8-K filings from the v1 watchlist.

    Returns list of {ticker, cik, accession, filed_date, exhibit_text}.
    Exhibit text is ready for Claude classification in Phase 3.
    """
    since_date = (datetime.date.today() - datetime.timedelta(days=since_days)).isoformat()
    new_filings = []

    async with EDGARClient(user_agent=settings.sec_user_agent) as edgar:
        for ticker, cik in V1_WATCHLIST:
            try:
                submissions = await edgar.get_submissions(cik)
                recent_8ks = edgar.get_recent_8k_accessions(
                    submissions, since_date=since_date, max_results=3
                )
            except Exception as exc:
                log.warning("edgar.check.error", ticker=ticker, cik=cik, error=str(exc))
                continue

            for filing in recent_8ks:
                accession = filing["accession"]
                try:
                    index = await edgar.get_filing_index(cik, accession)
                    exhibits = edgar.find_exhibit_99_docs(index)
                except Exception as exc:
                    log.warning("edgar.index.error", ticker=ticker, accession=accession, error=str(exc))
                    continue

                for exhibit in exhibits:
                    if not exhibit["url"]:
                        continue
                    try:
                        text = await edgar.fetch_exhibit_text(exhibit["url"])
                        new_filings.append({
                            "ticker": ticker,
                            "cik": cik,
                            "accession": filing["accession_formatted"],
                            "filed_date": filing["filed_date"],
                            "exhibit_type": exhibit["type"],
                            "exhibit_url": exhibit["url"],
                            "exhibit_text": text,
                            "source": "SEC_8K",
                        })
                        log.info(
                            "edgar.filing.found",
                            ticker=ticker,
                            filed_date=filing["filed_date"],
                            exhibit=exhibit["type"],
                        )
                    except Exception as exc:
                        log.warning("edgar.exhibit.error", ticker=ticker, error=str(exc))

            await asyncio.sleep(0.2)

    log.info("edgar.check.complete", new_filings=len(new_filings))
    return new_filings
