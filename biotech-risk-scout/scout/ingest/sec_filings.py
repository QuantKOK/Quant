"""SEC EDGAR company-submissions ingestion."""

from functools import lru_cache
import json
import os
from typing import Any, Dict, Iterable, List, Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

SEC_TICKER_CIK_URL = "https://www.sec.gov/files/company_tickers.json"
SEC_SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
DEFAULT_USER_AGENT = "BiotechRiskScout/0.1 contact@example.com"

FINANCING_FORMS = {
    "S-1",
    "S-1/A",
    "S-3",
    "S-3/A",
    "S-3ASR",
    "S-3ASR/A",
    "424B2",
    "424B3",
    "424B5",
    "FWP",
    "POS AM",
}


class SecClientError(RuntimeError):
    """Raised when the SEC client cannot resolve or fetch a dataset."""


def _get_user_agent() -> str:
    return os.getenv("SEC_USER_AGENT", DEFAULT_USER_AGENT)


def _fetch_json(url: str) -> Dict[str, Any]:
    request = Request(
        url,
        headers={
            "User-Agent": _get_user_agent(),
            "Accept-Encoding": "gzip, deflate",
            "Accept": "application/json",
        },
    )

    try:
        with urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        raise SecClientError(f"SEC request failed with HTTP {exc.code}: {url}") from exc
    except URLError as exc:
        raise SecClientError(f"SEC request failed: {url}; {exc.reason}") from exc
    except json.JSONDecodeError as exc:
        raise SecClientError(f"SEC returned invalid JSON: {url}") from exc


@lru_cache(maxsize=1)
def fetch_ticker_cik_map() -> Dict[str, Dict[str, Any]]:
    raw = _fetch_json(SEC_TICKER_CIK_URL)
    mapping: Dict[str, Dict[str, Any]] = {}

    for record in raw.values():
        ticker = str(record.get("ticker", "")).upper().strip()
        cik = record.get("cik_str")
        title = record.get("title")
        if ticker and cik is not None:
            mapping[ticker] = {"ticker": ticker, "cik": int(cik), "title": title}

    return mapping


def lookup_company(ticker: str) -> Dict[str, Any]:
    normalized = ticker.upper().strip()
    company = fetch_ticker_cik_map().get(normalized)
    if not company:
        raise SecClientError(f"Ticker not found in SEC company_tickers.json: {normalized}")
    return company


def cik_to_padded(cik: int) -> str:
    return f"{cik:010d}"


def fetch_company_submissions(ticker: str) -> Dict[str, Any]:
    company = lookup_company(ticker)
    padded_cik = cik_to_padded(company["cik"])
    url = SEC_SUBMISSIONS_URL.format(cik=padded_cik)
    submissions = _fetch_json(url)

    return {
        "ticker": company["ticker"],
        "cik": company["cik"],
        "padded_cik": padded_cik,
        "company_name": company.get("title") or submissions.get("name"),
        "submissions": submissions,
        "source_url": url,
    }


def _recent_rows(recent: Dict[str, List[Any]], limit: int) -> List[Dict[str, Any]]:
    fields = [key for key, value in recent.items() if isinstance(value, list)]
    if not fields:
        return []

    row_count = min(limit, *(len(recent[field]) for field in fields))
    rows: List[Dict[str, Any]] = []
    for index in range(row_count):
        rows.append({field: recent[field][index] for field in fields})
    return rows


def _latest_form(rows: Iterable[Dict[str, Any]], forms: Iterable[str]) -> Optional[Dict[str, Any]]:
    wanted = {form.upper() for form in forms}
    for row in rows:
        if str(row.get("form", "")).upper() in wanted:
            return row
    return None


def _summarize_row(row: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not row:
        return None
    return {
        "form": row.get("form"),
        "filing_date": row.get("filingDate"),
        "report_date": row.get("reportDate"),
        "accession_number": row.get("accessionNumber"),
        "primary_document": row.get("primaryDocument"),
        "description": row.get("primaryDocDescription"),
    }


def _summarize_rows(rows: Iterable[Dict[str, Any]], limit: int = 12) -> List[Dict[str, Any]]:
    return [_summarize_row(row) for row in list(rows)[:limit] if row]


def _has_recent_financing_form(rows: Iterable[Dict[str, Any]]) -> bool:
    return any(str(row.get("form", "")).upper() in FINANCING_FORMS for row in rows)


def fetch_sec_filings(ticker: str, recent_limit: int = 40) -> Dict[str, Any]:
    company_submissions = fetch_company_submissions(ticker)
    submissions = company_submissions["submissions"]
    recent = submissions.get("filings", {}).get("recent", {})
    rows = _recent_rows(recent, recent_limit)
    has_financing_form = _has_recent_financing_form(rows)

    return {
        "ticker": company_submissions["ticker"],
        "cik": company_submissions["cik"],
        "padded_cik": company_submissions["padded_cik"],
        "company_name": company_submissions["company_name"],
        "sic": submissions.get("sic"),
        "sic_description": submissions.get("sicDescription"),
        "exchanges": submissions.get("exchanges", []),
        "tickers": submissions.get("tickers", []),
        "fiscal_year_end": submissions.get("fiscalYearEnd"),
        "source_url": company_submissions["source_url"],
        "recent_filings": _summarize_rows(rows),
        "latest_10q": _summarize_row(_latest_form(rows, {"10-Q", "10-Q/A"})),
        "latest_10k": _summarize_row(_latest_form(rows, {"10-K", "10-K/A"})),
        "latest_8k": _summarize_row(_latest_form(rows, {"8-K", "8-K/A"})),
        "has_shelf": has_financing_form,
        "has_recent_financing_form": has_financing_form,
        "cash_runway_months": None,
        "burn_rate": None,
        "notes": "Real SEC company-submissions metadata fetched. Cash-runway extraction still needs XBRL or full filing parsing.",
    }
