# Real-Trial Cohort Acquisition & Adjudication Workbench

> **Scientific-risk research only — not investment advice.** This workbench
> discovers and *freezes* real clinical-trial source records for later human
> adjudication. It does not assign scientific outcomes, produce probabilities,
> or recommend any trade.

Source: ClinicalTrials.gov API v2 (<https://clinicaltrials.gov/data-api/api>).

## What this milestone does (and does not do)

- **Does:** query the API v2, deterministically filter candidates, freeze each
  full-study response immutably (canonical JSON + SHA-256), build a manifest,
  and generate a human-adjudication queue plus a data-quality report.
- **Does not:** assign `met`/`not_met`/`indeterminate`/`censored`, train a model,
  or convert registry status/"has results" into an efficacy label.

## Cohort selection protocol (`pilot-2020-2023-phase3-v1`)

A trial is **eligible** only if all hold (every failing reason is recorded):

| Criterion | Rule |
| --- | --- |
| Study type | `INTERVENTIONAL` |
| Phase | contains `PHASE3` |
| Intervention | at least one `DRUG` or `BIOLOGICAL` |
| Allocation | `RANDOMIZED` |
| Lead sponsor class | `INDUSTRY` |
| Results | posted results present |
| Primary completion | between `2020-01-01` and `2023-12-31` |
| Enrollment | ≥ 50 participants |

Candidates are sorted by NCT ID ascending; the pilot freezes the **first 50
eligible** (`--max-studies`, hard upper bound 100). A coarse server-side filter
narrows the query; **authoritative eligibility is always re-checked client-side**
and each failing exclusion reason is counted in the manifest and
`data_quality_report.json`.

### Exact query

- Version endpoint: `GET /api/v2/version` (retains `dataTimestamp`)
- Studies endpoint: `GET /api/v2/studies` with
  - `filter.advanced = AREA[StudyType]INTERVENTIONAL AND AREA[Phase]PHASE3 AND AREA[DesignAllocation]RANDOMIZED AND AREA[LeadSponsorClass]INDUSTRY AND AREA[PrimaryCompletionDate]RANGE[2020-01-01,2023-12-31]`
  - `aggFilters = results:with`
  - `pageSize = 50`, `format = json`, `countTotal = true`
- The exact parameters sent are recorded verbatim in the manifest `query` block.

**NCT ordering is client-side.** The API v2 does not support sorting by NCT ID
(`sort=NCTId` returns HTTP 400). Discovery therefore scans candidates, then sorts
eligible NCT IDs ascending and applies the pilot cap. A full authoritative build
scans all pages and fails rather than silently truncate at the 200-page safety
ceiling. `--scan-page-limit` bounds the scan only for smoke checks; bounded
selection is recorded in the manifest and raised as a high-severity
data-quality finding.

Each selected study is fetched individually and re-evaluated against every
criterion before freezing. If the full record changed since the search page and
is no longer eligible, it is excluded and the next eligible NCT ID backfills the
slot.

## Selection-bias limitations

This pilot is **not population-representative** and must not be used for base-rate
or prevalence estimates. Known biases include:

- **Results-availability bias:** requiring posted results over-represents sponsors
  and programs that comply with results reporting.
- **Cap + NCT ordering:** taking the first 50 by NCT ID is an arbitrary, non-random
  slice, not a sample.
- **Industry-only, Phase-3-only, drug/biological-only:** deliberately narrow.
- **Registry completeness varies** across sponsors and years.

## Leakage warning (critical)

Candidate discovery uses **current** registry records. Current records may have
been edited *after* a trial's outcome was known (endpoints, status, enrollment,
and results can change over time). Therefore:

- Every queue entry carries `historical_feature_snapshot_status: "unresolved"`.
- Frozen snapshots are **evidence for adjudication**, **not** leakage-free,
  point-in-time model features. Do not train predictive models on these
  current-record fields as if they were known at prediction time.
- Establishing point-in-time features requires dated historical registry
  versions, which this milestone does not collect.

## Human adjudication procedure

1. Work the `adjudication_queue.jsonl` entries (each `review_status: pending`).
2. For each trial, read the frozen snapshot and the primary outcome definition.
3. Gather **dated public evidence** and determine the outcome against the
   pre-specified endpoint — never from registry status or the mere presence of
   results.
4. Record the outcome only in the separate, strict `HistoricalOutcomeRecord`
   schema (`scout/outcomes/`), which requires rationale, dated evidence, and
   point-in-time integrity. The queue schema is intentionally distinct and is
   never promoted or weakened into that schema.

### Evidence hierarchy (highest to lowest)

1. Peer-reviewed primary publication of the trial's results.
2. ClinicalTrials.gov posted results / statistical analysis plan.
3. Regulatory documents (e.g., FDA/EMA reviews, labels).
4. Sponsor topline press release.

**Conflicts between sources must be preserved and escalated, not silently
resolved.** Record each conflicting source and its date; escalate for a second
adjudicator rather than picking one silently. A conflict is itself a finding.

## Artifacts written to the output directory

```
<output>/
  raw/<NCT>.json            # one immutable canonical full-study snapshot per NCT
  manifest.json             # rules, exact query, API/data timestamps, per-file + overall hashes
  adjudication_queue.jsonl  # deterministic pending queue (no outcome labels)
  exclusions.jsonl          # every examined-but-excluded NCT with reasons
  data_quality_report.json  # critical/high/medium/low findings
```

## CLI

```bash
export CTG_USER_AGENT="BiotechRiskScout research (you@example.com)"

# Discover + freeze (default cap 50, hard max 100)
python biotech-risk-scout/app/cohort.py discover --output <dir> --max-studies 50

# Verify manifest, snapshot hashes, and byte-for-byte queue reproduction
python biotech-risk-scout/app/cohort.py verify --output <dir>

# Non-sensitive summary (never prints raw payloads)
python biotech-risk-scout/app/cohort.py summary --output <dir>

# Deterministically rebuild the queue from frozen snapshots
python biotech-risk-scout/app/cohort.py rebuild-queue --output <dir>
```

`CTG_USER_AGENT` is required (the client refuses to run without a descriptive
User-Agent, per API etiquette). Existing cohort directories are never silently
overwritten, including partial directories without a manifest. Pass `--force`
to replace one. Replacement is staged separately and promoted only after the
new cohort is complete, so stale snapshots are not retained.

## Integrity & reproducibility

- Each snapshot is canonical JSON hashed with SHA-256; the manifest records
  per-file hashes and an overall manifest hash. The exclusions log and
  data-quality report are hashed as well.
- The adjudication queue reproduces **byte-for-byte** from the frozen snapshots,
  so `verify` detects missing, extra, edited, or reordered records.
- Snapshot paths are constrained to canonical `raw/NCT########.json` paths;
  manifest verification and queue rebuilding reject path traversal.
- File writes are atomic, and complete cohorts are assembled in a staging
  directory before promotion.
