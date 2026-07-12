# Decision packets (pre-trade research notes)

A **decision packet** is the disciplined human research note captured *before* a
possible trade becomes a logged candidate. It is a research artifact, **not a
trade, an order, or investment advice**. Like the rest of MacroEdge it is
offline only: no network, no credentials, no order execution.

Packets sit between a verified contract observation and the trade-candidate
journal:

```
contract observation  ->  decision packet  ->  (only if candidate_ok)  trade candidate
   (what the market      (what you think and       (a logged, edge/risk-gated
    looked like)          why, before acting)        research position)
```

Most observations should never become candidates. The decision packet is where
that judgement is written down and preserved, including the cases where you
decide **not** to trade.

## What a packet captures

Human inputs:

- `thesis_summary` — your concise thesis for the selected side
- `data_sources` — cited http(s) sources behind the thesis
- `evidence_as_of` — timestamp your evidence is current to (defaults to the
  observation's `observed_at`)
- `fair_probability` — your fair probability for the **selected side**
- `side` — `YES` or `NO`
- `intent` — `no_trade`, `paper`, or `tiny_live`
- `risk_notes` — how you would size/limit risk
- `disconfirming_evidence` — evidence that argues against the thesis (required,
  to force the discipline of considering the other side)
- `decision` — `observe_only`, `candidate_ok`, or `reject`

Computed, from the verified observation:

- `market_implied_probability` for the selected side (the same number a later
  candidate would use as its entry price)
- `gross_edge` = `fair_probability - market_implied_probability`
- `edge_percentage_points`
- `clears_edge_threshold` — whether the edge meets the configured `min_edge`
- `suggested_next_action` — a **workflow** label (e.g. "keep observing",
  "eligible to seed a candidate for human review"). This is never a
  buy/sell/hold recommendation.

Each packet is deterministic given `packet_id` and `created_at`, and carries a
`packet_hash` (SHA-256 over the record minus the hash) for tamper detection,
mirroring `contract_hash` / `candidate_hash`. `verify` recomputes the edge math,
threshold, and next-action label, so silently editing any field is detected.

## CLI

```bash
# Build a packet from a verified observation and print its summary
py -3 -m macroedge decision-packet validate \
  --input macroedge/contract-observation.example.json \
  --side YES --fair-probability 0.53 \
  --thesis-summary "Manual CPI thesis from cited sources." \
  --data-source https://www.bls.gov/cpi/ \
  --intent paper --decision candidate_ok \
  --risk-notes "Tiny size; single-event exposure under cap." \
  --disconfirming-evidence "Shelter disinflation could pull the print lower." \
  --packet-id cpi-2026jun --created-at 2026-07-15T02:00:00+00:00

# Write the canonical packet JSON for the research record
py -3 -m macroedge decision-packet emit --output macroedge/decision-packet.example.json ...same flags...

# Re-verify an emitted packet later (hash + schema + edge math)
py -3 -m macroedge decision-packet verify --input macroedge/decision-packet.example.json
```

Pass both `--packet-id` and `--created-at` for a reproducible `packet_hash`.
Use `--min-edge` to override the default edge threshold.

## Seeding a candidate from a packet

Only a packet whose `decision` is `candidate_ok` should become a trade candidate.
`macroedge.decision_packet.candidate_seed_from_packet(packet)` verifies the
packet and returns the analyst inputs (`side`, `fair_probability`,
`thesis_summary`, `data_sources`, `evidence_as_of`) to pass into
`macroedge.candidate_builder.build_candidate_from_observation` alongside the same
verified observation and the risk sizing. It raises for any other decision. It
does not build a candidate or place a trade — it only carries the human inputs
forward, keeping a clean audit trail from observation → packet → candidate.
