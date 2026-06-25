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
    cash: Optional[float] = None
    monthly_burn: Optional[float] = None
    operating_cash_flow: Optional[float] = None
    trial_count: int = 0
    active_trial_count: int = 0
    sponsor_names: list[str] = field(default_factory=list)
    extra: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_sources(cls, ticker: str, filings: Dict[str, Any], trials: Dict[str, Any]) -> "ResearchCard":
        """Build a research card from ingestion outputs."""
        days_until_event = trials.get("days_until_event")
        catalyst = trials.get("upcoming_catalyst", "Unknown catalyst")
        cash_runway = filings.get("cash_runway_months")
        has_financing_form = bool(filings.get("has_recent_financing_form") or filings.get("has_shelf"))
        trial_list = trials.get("trials", []) or []
        active_trial_count = _count_active_trials(trial_list)

        why_parts = []
        if days_until_event is not None:
            why_parts.append(f"Upcoming catalyst in {days_until_event} days")
        else:
            why_parts.append("No dated active catalyst identified yet")

        if filings.get("company_name"):
            why_parts.append(f"SEC filer matched: {filings['company_name']}")

        if trial_list:
            why_parts.append(f"{len(trial_list)} ClinicalTrials.gov studies found")

        if cash_runway is None:
            why_parts.append("cash runway unknown")
        else:
            why_parts.append(f"cash runway {cash_runway} months")

        if has_financing_form:
            why_parts.append("recent financing-related filing detected")

        dilution = _infer_dilution_risk(cash_runway, days_until_event, has_financing_form)

        main_risk = "Binary trial outcome"
        if dilution in {"High", "Watch"}:
            main_risk = "Financing risk before/around catalyst plus binary trial outcome"
        if not trial_list:
            main_risk = "No trial match found yet; sponsor mapping may need review"

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
            cash=filings.get("cash"),
            monthly_burn=filings.get("monthly_burn") or filings.get("burn_rate"),
            operating_cash_flow=filings.get("operating_cash_flow"),
            trial_count=len(trial_list),
            active_trial_count=active_trial_count,
            sponsor_names=trials.get("sponsor_names", []) or [],
            extra={
                "filing_notes": filings.get("notes"),
                "trial_notes": trials.get("notes"),
                "sec_source_url": filings.get("source_url"),
                "recent_filings": filings.get("recent_filings", []),
                "cash_fact": filings.get("cash_fact"),
                "operating_cash_flow_fact": filings.get("operating_cash_flow_fact"),
                "trials": trial_list,
            },
        )

    def to_text(self) -> str:
        """Return a multi-line string representation of the research card."""
        lines = [
            "=" * 72,
            f"BIOTECH RISK SCOUT: {self.ticker}",
            "=" * 72,
        ]

        if self.company_name:
            lines.append(f"Company: {self.company_name}")

        lines.extend(
            [
                f"Why surfaced: {self.why_surfaced}",
                "",
                "Catalyst",
                "--------",
                f"Upcoming catalyst: {self.catalyst}",
                f"Trial count: {self.trial_count} total / {self.active_trial_count} active-like",
                f"Evidence quality: {self.evidence_quality}",
            ]
        )

        if self.sponsor_names:
            lines.append(f"Sponsor match: {', '.join(self.sponsor_names[:3])}")

        lines.extend(
            [
                "",
                "Financial Runway",
                "-----------------",
                f"Cash: {_format_money(self.cash)}",
                f"Operating cash flow: {_format_money(self.operating_cash_flow)}",
                f"Monthly burn: {_format_money(self.monthly_burn)}",
                f"Runway: {_format_optional_number(self.cash_runway_months)} months",
                f"Dilution risk: {self.dilution_risk}",
                "",
                "SEC Filings",
                "-----------",
            ]
        )

        lines.append(f"Latest 10-Q: {_format_filing(self.latest_10q)}")
        lines.append(f"Latest 10-K: {_format_filing(self.latest_10k)}")
        lines.append(f"Latest 8-K: {_format_filing(self.latest_8k)}")

        lines.extend(
            [
                "",
                "Risk Read",
                "---------",
                f"Main risk: {self.main_risk}",
                f"Next diligence steps: {self.next_steps}",
            ]
        )

        filing_notes = self.extra.get("filing_notes")
        trial_notes = self.extra.get("trial_notes")
        if filing_notes or trial_notes:
            lines.extend(["", "Data Notes", "----------"])
            if filing_notes:
                lines.append(f"SEC: {filing_notes}")
            if trial_notes:
                lines.append(f"Trials: {trial_notes}")

        return "\n".join(lines)

    def __str__(self) -> str:
        return self.to_text()


def _count_active_trials(trials: list[Dict[str, Any]]) -> int:
    active_statuses = {
        "RECRUITING",
        "ACTIVE_NOT_RECRUITING",
        "ENROLLING_BY_INVITATION",
        "NOT_YET_RECRUITING",
    }
    return sum(1 for trial in trials if str(trial.get("status", "")).upper() in active_statuses)


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


def _format_money(value: Optional[float]) -> str:
    if value is None:
        return "Unknown"

    value = float(value)
    sign = "-" if value < 0 else ""
    absolute = abs(value)

    if absolute >= 1_000_000_000:
        return f"{sign}${absolute / 1_000_000_000:.2f}B"
    if absolute >= 1_000_000:
        return f"{sign}${absolute / 1_000_000:.1f}M"
    if absolute >= 1_000:
        return f"{sign}${absolute / 1_000:.1f}K"
    return f"{sign}${absolute:,.0f}"


def _format_filing(filing: Optional[Dict[str, Any]]) -> str:
    if not filing:
        return "Not found"
    form = filing.get("form", "Unknown form")
    filed = filing.get("filing_date", "unknown date")
    doc = filing.get("primary_document") or "no primary document listed"
    return f"{form} filed {filed} ({doc})"
