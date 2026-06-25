"""SEC EDGAR company-submissions and company-facts ingestion."""

from datetime import datetime
from functools import lru_cache
import json
import os
from typing import Any, Dict, Iterable, List, Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

SEC_TICKER_CIK_URL = "https://www.sec.gov/files/company_tickers.json"
SEC_SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
SEC_COMPANY_FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
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

CASH_TAGS = [
    "CashAndCashEquivalentsAtCarryingValue",
    "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents",
]

OPERATING_CASH_FLOW_TAGS = [
    "NetCashProvidedByUsedInOperatingActivities",
]


class SecClientError(RuntimeError):
    """Raised when the SEC client cannot resolve or fetch a dataset."""


def _get_user_agent() -> str:
    return os.getenv("SEC_USER_AGENT", DEFAULT_USER_AGENT)


def _fetch_json(url: str) -> Dict[str, Any]:
    request = Request(
        url,
        headers={
            "User-Agent": _get_user_agent(),
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


def fetch_company_facts_by_cik(cik: int) -> Dict[str, Any]:
    """Fetch SEC XBRL company-facts JSON for a CIK."""
    padded_cik = cik_to_padded(cik)
    return _fetch_json(SEC_COMPANY_FACTS_URL.format(cik=padded_cik))


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


def _parse_date(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d")
    except ValueError:
        return None


def _fact_end_date(fact: Dict[str, Any]) -> datetime:
    return _parse_date(fact.get("end")) or datetime.min


def _fact_filed_date(fact: Dict[str, Any]) -> datetime:
    return _parse_date(fact.get("filed")) or datetime.min


def _duration_days(fact: Dict[str, Any]) -> Optional[int]:
    start = _parse_date(fact.get("start"))
    end = _parse_date(fact.get("end"))
    if not start or not end:
        return None
    days = (end - start).days
    return days if days > 0 else None


def _facts_for_tag(company_facts: Dict[str, Any], tag: str, unit: str = "USD") -> List[Dict[str, Any]]:
    fact = company_facts.get("facts", {}).get("us-gaap", {}).get(tag, {})
    units = fact.get("units", {})
    return list(units.get(unit, []))


def _latest_instant_fact(company_facts: Dict[str, Any], tags: Iterable[str]) -> Optional[Dict[str, Any]]:
    candidates: List[Dict[str, Any]] = []
    for tag in tags:
        for fact in _facts_for_tag(company_facts, tag):
            if fact.get("val") is not None and fact.get("end"):
                fact_copy = dict(fact)
                fact_copy["tag"] = tag
                candidates.append(fact_copy)

    if not candidates:
        return None
    return max(candidates, key=lambda fact: (_fact_end_date(fact), _fact_filed_date(fact)))


def _latest_duration_fact(company_facts: Dict[str, Any], tags: Iterable[str]) -> Optional[Dict[str, Any]]:
    candidates: List[Dict[str, Any]] = []
    for tag in tags:
        for fact in _facts_for_tag(company_facts, tag):
            if fact.get("val") is None or not fact.get("start") or not fact.get("end"):
                continue
            duration = _duration_days(fact)
            if duration is None or duration < 20:
                continue
            fact_copy = dict(fact)
            fact_copy["tag"] = tag
            fact_copy["duration_days"] = duration
            candidates.append(fact_copy)

    if not candidates:
        return None
    return max(candidates, key=lambda fact: (_fact_end_date(fact), _fact_filed_date(fact)))


def _summarize_fact(fact: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not fact:
        return None
    return {
        "tag": fact.get("tag"),
        "value": fact.get("val"),
        "start": fact.get("start"),
        "end": fact.get("end"),
        "filed": fact.get("filed"),
        "form": fact.get("form"),
        "fiscal_year": fact.get("fy"),
        "fiscal_period": fact.get("fp"),
        "duration_days": fact.get("duration_days"),
    }


def _calculate_cash_runway(company_facts: Dict[str, Any]) -> Dict[str, Any]:
    """
    Estimate cash runway from SEC XBRL facts.

    This is a first-pass heuristic. It uses the latest reported cash fact and
    the latest operating cash-flow duration fact. If operating cash flow is
    negative, monthly burn is annualized from the reported period length.
    """
    cash_fact = _latest_instant_fact(company_facts, CASH_TAGS)
    ocf_fact = _latest_duration_fact(company_facts, OPERATING_CASH_FLOW_TAGS)

    cash_value = cash_fact.get("val") if cash_fact else None
    ocf_value = ocf_fact.get("val") if ocf_fact else None
    duration = ocf_fact.get("duration_days") if ocf_fact else None

    monthly_burn = None
    runway_months = None

    if cash_value is not None and ocf_value is not None and duration:
        months = float(duration) / 30.44
        if months > 0 and float(ocf_value) < 0:
            monthly_burn = abs(float(ocf_value)) / months
            if monthly_burn > 0:
                runway_months = float(cash_value) / monthly_burn

    return {
        "cash": cash_value,
        "operating_cash_flow": ocf_value,
        "monthly_burn": monthly_burn,
        "cash_runway_months": round(runway_months, 1) if runway_months is not None else None,
        "cash_fact": _summarize_fact(cash_fact),
        "operating_cash_flow_fact": _summarize_fact(ocf_fact),
    }


def fetch_sec_filings(ticker: str, recent_limit: int = 40) -> Dict[str, Any]:
    company_submissions = fetch_company_submissions(ticker)
    submissions = company_submissions["submissions"]
    recent = submissions.get("filings", {}).get("recent", {})
    rows = _recent_rows(recent, recent_limit)
    has_financing_form = _has_recent_financing_form(rows)

    runway = {
        "cash": None,
        "operating_cash_flow": None,
        "monthly_burn": None,
        "cash_runway_months": None,
        "cash_fact": None,
        "operating_cash_flow_fact": None,
    }
    company_facts_error = None
    try:
        company_facts = fetch_company_facts_by_cik(company_submissions["cik"])
        runway = _calculate_cash_runway(company_facts)
    except SecClientError as exc:
        company_facts_error = str(exc)

    notes = "Real SEC company-submissions metadata fetched."
    if runway.get("cash_runway_months") is not None:
        notes += " Cash runway estimated from SEC company-facts XBRL data."
    elif company_facts_error:
        notes += f" Company-facts cash runway extraction failed: {company_facts_error}"
    else:
        notes += " Cash runway could not be estimated from available XBRL facts."

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
        "company_facts_error": company_facts_error,
        "recent_filings": _summarize_rows(rows),
        "latest_10q": _summarize_row(_latest_form(rows, {"10-Q", "10-Q/A"})),
        "latest_10k": _summarize_row(_latest_form(rows, {"10-K", "10-K/A"})),
        "latest_8k": _summarize_row(_latest_form(rows, {"8-K", "8-K/A"})),
        "has_shelf": has_financing_form,
        "has_recent_financing_form": has_financing_form,
        "cash": runway.get("cash"),
        "operating_cash_flow": runway.get("operating_cash_flow"),
        "monthly_burn": runway.get("monthly_burn"),
        "cash_runway_months": runway.get("cash_runway_months"),
        "cash_fact": runway.get("cash_fact"),
        "operating_cash_flow_fact": runway.get("operating_cash_flow_fact"),
        "burn_rate": runway.get("monthly_burn"),
        "notes": notes,
    }
