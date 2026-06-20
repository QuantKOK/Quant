"""
Research card representation for Biotech Risk Scout.

This module defines a simple data structure to hold the key elements of a
research memo. It also provides convenience methods to construct a card from
ingestion layer outputs and to produce a human-readable representation.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, Optional


@dataclass
class ResearchCard:
    """Container for summarizing a biotechnology special situations ticker."""

    ticker: str
    why_surfaced: str
    catalyst: str
    cash_runway_months: Optional[float]
    dilution_risk: str
    evidence_quality: str
    main_risk: str
    next_steps: str
    company_name: Optional[str] = None
    latest_10q: Optional[Dict[str, Any]] = None
    latest_10k: Optional[Dict[str, Any]] = None
    latest_8k: Optional[Dict[str, Any]] = None
    extra: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_sources(cls, ticker: str, filings: Dict[str, Any], trials: Dict[str, Any]) -> "ResearchCard":
        """
        Build a research card from ingestion outputs.
        """
        days_until_event = trials.get("days_until_event")
        catalyst = trials.get("upcoming_catalyst", "Unknown catalyst")
        cash_runway = filings.get("cash_runway_months")
        has_financing_form = bool(filings.get("has_recent_financing_form") or filings.get("has_shelf"))

        why_parts = []
        if days_until_event is not None:
            why_parts.append(f"Upcoming {catalyst} in {days_until_event} days")
        else:
            why_parts.append(f"Upcoming {catalyst}")

        if filings.get("company_name"):
            why_parts.append(f"SEC filer matched: {filings['company_name']}")

        if cash_runway is None:
            why_parts.append("cash runway not extracted yet")
        else:
            why_parts.append(f"cash runway {cash_runway} months")

        if has_financing_form:
            why_parts.append("recent financing-related filing detected")

        dilution = _infer_dilution_risk(cash_runway, days_until_event, has_financing_form)

        main_risk = "Binary trial outcome"
        if dilution in {"High", "Watch"}:
            main_risk = "Financing risk before/around catalyst plus binary trial outcome"

        return cls(
            ticker=ticker.upper(),
            company_name=filings.get("company_name"),
            why_surfaced="; ".join(why_parts),
            catalyst=catalyst,
            cash_runway_months=cash_runway,
            dilution_risk=dilution,
            evidence_quality=trials.get("evidence_quality", "Unknown"),
            main_risk=main_risk,
            next_steps="Read latest 10-Q/10-K, review recent 8-Ks, inspect trial protocol, and monitor financing risk.",
            latest_10q=filings.get("latest_10q"),
            latest_10k=filings.get("latest_10k"),
            latest_8k=filings.get("latest_8k"),
            extra={
                "filing_notes": filings.get("notes"),
                "trial_notes": trials.get("notes"),
                "sec_source_url": filings.get("source_url"),
                "recent_filings": filings.get("recent_filings", []),
            },
        )

    def to_text(self) -> str:
        """Return a multi-line string representation of the research card."""
        lines = [
            f"Ticker: {self.ticker}",
        ]

        if self.company_name:
            lines.append(f"Company: {self.company_name}")

        lines.extend(
            [
                f"Why it surfaced: {self.why_surfaced}",
                f"Catalyst: {self.catalyst}",
                f"Cash runway (months): {_format_optional_number(self.cash_runway_months)}",
                f"Dilution risk: {self.dilution_risk}",
                f"Evidence quality: {self.evidence_quality}",
                f"Main risk: {self.main_risk}",
            ]
        )

        if self.latest_10q:
            lines.append(f"Latest 10-Q: {_format_filing(self.latest_10q)}")
        if self.latest_10k:
            lines.append(f"Latest 10-K: {_format_filing(self.latest_10k)}")
        if self.latest_8k:
            lines.append(f"Latest 8-K: {_format_filing(self.latest_8k)}")

        lines.append(f"Next diligence steps: {self.next_steps}")
        return "\n".join(lines)

    def __str__(self) -> str:
        return self.to_text()


def _infer_dilution_risk(cash_runway: Optional[float], days_until_event: Any, has_financing_form: bool) -> str:
    """Infer a first-pass dilution risk label from runway and filing metadata."""
    if cash_runway is None:
        return "Watch" if has_financing_form else "Unknown"

    if days_until_event is None:
        return "Watch" if has_financing_form else "Unknown"

    months_until_event = float(days_until_event) / 30.0
    if cash_runway < months_until_event:
        return "High"
    if has_financing_form:
        return "Watch"
    return "Low"


def _format_optional_number(value: Optional[float]) -> str:
    if value is None:
        return "Unknown"
    return str(value)


def _format_filing(filing: Dict[str, Any]) -> str:
    form = filing.get("form", "Unknown form")
    filed = filing.get("filing_date", "unknown date")
    doc = filing.get("primary_document") or "no primary document listed"
    return f"{form} filed {filed} ({doc})"
