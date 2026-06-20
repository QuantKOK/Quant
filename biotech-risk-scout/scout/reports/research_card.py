"""
Research card representation for Biotech Risk Scout.

This module defines a simple data structure to hold the key elements of a
research memo. It also provides convenience methods to construct a card from
ingestion layer outputs and to produce a human-readable representation.
"""

from dataclasses import dataclass, field
from typing import Any, Dict


@dataclass
class ResearchCard:
    """Container for summarizing a biotechnology special situations ticker."""

    ticker: str
    why_surfaced: str
    catalyst: str
    cash_runway_months: float
    dilution_risk: str
    evidence_quality: str
    main_risk: str
    next_steps: str
    extra: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_sources(cls, ticker: str, filings: Dict[str, Any], trials: Dict[str, Any]) -> "ResearchCard":
        """
        Build a research card from ingestion outputs.
        """
        why = (
            f"Upcoming {trials['upcoming_catalyst']} in {trials['days_until_event']} days; "
            f"cash runway {filings['cash_runway_months']} months"
        )

        dilution = "High" if filings["cash_runway_months"] < trials["days_until_event"] / 30 else "Low"

        return cls(
            ticker=ticker.upper(),
            why_surfaced=why,
            catalyst=trials["upcoming_catalyst"],
            cash_runway_months=filings["cash_runway_months"],
            dilution_risk=dilution,
            evidence_quality=trials["evidence_quality"],
            main_risk="Binary trial outcome",
            next_steps="Read latest 10-Q, clinical protocol; monitor financing risk.",
            extra={
                "filing_notes": filings.get("notes"),
                "trial_notes": trials.get("notes"),
            },
        )

    def to_text(self) -> str:
        """Return a multi-line string representation of the research card."""
        lines = [
            f"Ticker: {self.ticker}",
            f"Why it surfaced: {self.why_surfaced}",
            f"Catalyst: {self.catalyst}",
            f"Cash runway (months): {self.cash_runway_months}",
            f"Dilution risk: {self.dilution_risk}",
            f"Evidence quality: {self.evidence_quality}",
            f"Main risk: {self.main_risk}",
            f"Next diligence steps: {self.next_steps}",
        ]
        return "\n".join(lines)

    def __str__(self) -> str:
        return self.to_text()
