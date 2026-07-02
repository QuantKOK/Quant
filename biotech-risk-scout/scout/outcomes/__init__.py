"""Auditable historical clinical-trial outcome dataset.

This package defines a strict, point-in-time record schema for scientific-risk
research and a deterministic dataset builder/verifier. It is intentionally
conservative: registry status (for example ``COMPLETED`` / ``TERMINATED``) is
kept strictly separate from the adjudicated ``scientific_outcome``, and no
probabilities or investment recommendations are permitted.

This is scientific-risk research tooling, not investment advice.
"""
