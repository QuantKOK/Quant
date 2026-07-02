# Historical Clinical-Trial Outcome Dataset

> **Scientific-risk research only — not investment advice.** This dataset records
> observed, adjudicated scientific outcomes of clinical trials for research into
> scientific risk. It contains no probabilities, price targets, position sizing,
> or buy/sell/hold recommendations, and none may be added.

This module (`scout/outcomes/`) builds an **auditable, point-in-time, deterministic**
dataset of historical clinical-trial outcomes. It is the data foundation for
future scientific-risk research. It does **not** include a predictive model, and
it never derives a scientific outcome from registry status.

## The core rule: registry status ≠ scientific outcome

ClinicalTrials.gov **registry status** (e.g., `COMPLETED`, `TERMINATED`,
`RECRUITING`) describes the operational state of a study. It is **not** an
efficacy result.

- A `COMPLETED` trial is **not** automatically a success. Trials routinely run to
  completion and miss their pre-specified primary endpoint.
- A `TERMINATED` trial is **not** automatically a failure. Studies are terminated
  for business, funding, enrollment, or portfolio reasons unrelated to efficacy.

The dataset therefore stores `registry_status` and `scientific_outcome` as two
independent fields. The builder never converts one into the other; the
`scientific_outcome` must be adjudicated by a human from cited evidence.

## Label taxonomy (`scientific_outcome`)

| Label | Meaning |
| --- | --- |
| `met` | The pre-specified endpoint in `outcome_definition` was met, per cited evidence. Requires a rationale and ≥1 HTTP(S) evidence URL. |
| `not_met` | The pre-specified endpoint was not met (including reported futility), per cited evidence. Requires a rationale and ≥1 HTTP(S) evidence URL. |
| `indeterminate` | Evidence exists but is genuinely ambiguous or conflicting; no confident met/not-met call. |
| `censored` | The trial cannot inform the endpoint (e.g., stopped for business reasons before an efficacy readout). **Not** a scientific failure. |

`censored` and `indeterminate` are deliberately **distinct**: `censored` means
"no efficacy signal is obtainable from this study," while `indeterminate` means
"there is a signal, but it is inconclusive."

## Record schema

Each record contains exactly these fields (unknown fields are rejected):

- `record_id` — unique, stable identifier
- `nct_id` — `NCT` followed by 8 digits
- `sponsor`
- `intervention` — the asset / intervention
- `indication`
- `phase`
- `prediction_cutoff_at` — ISO-8601 point-in-time "as of" timestamp with timezone
- `outcome_observed_at` — ISO-8601 with timezone; must be ≥ `prediction_cutoff_at`
- `registry_status` — the registry's operational status (kept separate)
- `scientific_outcome` — one of `met`, `not_met`, `indeterminate`, `censored`
- `outcome_definition` — the pre-specified endpoint being adjudicated
- `adjudication_rationale` — why the label was assigned
- `evidence_urls` — list of HTTP(S) provenance links
- `source_publication_dates` — ISO dates paired by position with
  `evidence_urls`; each must be ≥ `prediction_cutoff_at`
- `adjudicator` — who/what made the call
- `schema_version` — stamped by the builder (`1.0.0`)

## Point-in-time safeguards (no lookahead / leakage)

`prediction_cutoff_at` is the "as of" line. To keep the dataset usable for
honest, leakage-free research later:

- `outcome_observed_at` cannot predate `prediction_cutoff_at`.
- Every `source_publication_dates` entry cannot predate `prediction_cutoff_at` —
  evidence used to adjudicate the outcome must be published at or after the
  cutoff, so a record cannot secretly encode information from before its own
  cutoff.
- Timestamps must include a timezone and are normalized to UTC.

## Provenance requirements

- `met` / `not_met` require a non-empty `adjudication_rationale` **and** at least
  one HTTP(S) `evidence_urls` entry.
- `evidence_urls` and `source_publication_dates` must have equal lengths. Each
  URL is paired with the date at the same list position before deterministic
  pair sorting.
- All records require `outcome_definition`, `adjudication_rationale`, and
  `adjudicator` so every label is traceable and reviewable.
- Evidence URLs must use `http`/`https` schemes.

## Determinism, manifest, and verification

`build` validates and normalizes records, sorts them deterministically
(`nct_id`, `prediction_cutoff_at`, `outcome_observed_at`, `record_id`), and
writes canonical JSONL (sorted keys, `\n` newlines, deduplicated + sorted list
fields). The same source always produces byte-identical output.

The manifest records the schema version, generation time, record count, label
counts, registry-status counts, and the **SHA-256 hash of the dataset bytes**.
`verify` recomputes that hash, re-validates every record, and re-derives the
counts, so any tampering, corruption, or schema drift is detected.

## CLI

```bash
# Build a canonical dataset + manifest from the synthetic example
python biotech-risk-scout/app/outcomes.py build \
  --source biotech-risk-scout/data/outcomes/example_source.jsonl \
  --out biotech-risk-scout/data/outcomes/example_dataset.jsonl

# Verify it against its manifest (tamper detection)
python biotech-risk-scout/app/outcomes.py verify \
  --dataset biotech-risk-scout/data/outcomes/example_dataset.jsonl

# Print counts (labels and registry statuses shown separately)
python biotech-risk-scout/app/outcomes.py summary \
  --dataset biotech-risk-scout/data/outcomes/example_dataset.jsonl
```

Pass `--generated-at <ISO-8601>` to `build` for a fully reproducible manifest.

## Limitations

- The bundled `example_source.jsonl` is **clearly synthetic** (fictional
  sponsors, assets, `NCT0000000x` identifiers, and `example.com` links). It
  exists to exercise the schema and tooling, not for analysis.
- Adjudication is a human judgment about pre-specified endpoints; different
  adjudicators may disagree, which is why rationale and evidence are mandatory.
- This is a data foundation only. There is intentionally **no** predictive model,
  probability, or trade signal here, and registry status is never auto-converted
  into a scientific outcome.
