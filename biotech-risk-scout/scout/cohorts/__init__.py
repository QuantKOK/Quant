"""Reproducible real-trial cohort acquisition and adjudication workbench.

This package discovers candidate clinical trials from the ClinicalTrials.gov
API v2, freezes the raw source responses immutably (canonical JSON + SHA-256),
and generates a human-adjudication queue. It deliberately does **not** assign
scientific outcomes or train any model.

Scientific guardrails enforced here:

* Registry status is never treated as an efficacy label.
* "Has posted results" is never treated as ``met`` / ``not_met``.
* Current registry records may contain post-outcome edits, so every candidate
  is stamped ``historical_feature_snapshot_status: "unresolved"`` and is never
  presented as a leakage-free historical feature.

Scientific-risk research only. Not investment advice.
"""
