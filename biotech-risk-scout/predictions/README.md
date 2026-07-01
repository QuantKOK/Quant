# Prediction Ledger

This directory is the public, auditable record of clinical-trial risk
predictions. The ledger is append-only JSONL: each record includes the SHA-256
hash of the prior record, making edits, insertions, and reordering detectable.

A local hash chain is tamper-evident, not independently immutable. For external
proof, publish the ledger commit and head hash before trial outcomes are known.

Create a draft from `prediction-draft.example.json`, then append and verify:

```bash
python biotech-risk-scout/app/predictions.py append \
  --input biotech-risk-scout/predictions/prediction-draft.json
python biotech-risk-scout/app/predictions.py verify
python biotech-risk-scout/app/predictions.py head
```

Never delete incorrect predictions. Append a new superseding prediction with a
new timestamp and explain the revision in its rationale.
