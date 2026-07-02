"""First-pass human-adjudication evidence packets (drafts for second review).

This package assembles auditable evidence packets from *frozen* authoritative
cohort snapshots and prepares first-pass adjudication drafts. It is deliberately
separate from the final ``HistoricalOutcomeRecord`` schema and the cohort
acquisition tooling.

Hard rules enforced here:

* Outcomes are never inferred from registry status or the mere availability of
  posted results, and a secondary endpoint is never used as the primary result.
* A non-null proposed outcome is only ever a ``needs_second_review`` draft and
  must carry dated evidence, a rationale, and a confidence. These are drafts for
  a second human reviewer — not final labels and not training data.
* Current registry snapshots stay ``historical_feature_snapshot_status:
  "unresolved"`` (they may contain post-outcome edits).

Scientific-risk research only. Not investment advice.
"""
