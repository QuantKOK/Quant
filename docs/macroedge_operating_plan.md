# MacroEdge Operating Plan

MacroEdge is a macro event-contract research and risk system. The goal is not
to build a betting bot or chase fast returns. The goal is to test whether our
probability estimates beat market-implied probabilities after fees, spreads,
slippage, and human mistakes.

## Product framing

MacroEdge focuses on financial and macro event contracts:

- CPI and inflation releases
- unemployment and payroll releases
- Federal Reserve decision markets
- GDP and recession-style markets
- other contracts with clear financial or macro relevance

The system should be explainable as a small probability research desk:

> Estimate a fair probability, compare it with market-implied probability,
> filter for edge, size conservatively, journal the thesis, and measure
> calibration after settlement.

## Bankroll policy

Starting capital may be larger than the active bankroll. The active bankroll is
the only capital exposed to event-contract risk.

- Total capital reference: `$1,500`
- Active bankroll target: `$300-$500`
- Risk per trade: `$10-$25`
- Max exposure to one event: about `$50`
- Minimum edge: `8-10` percentage points after spread/fees
- No trade without written settlement rules and cited data sources

## Required trade-candidate fields

Every candidate must record:

- event name and type
- release and settlement timestamps
- official settlement source
- exact settlement rules
- market platform, contract ID, question, side, URL, and entry price
- market-implied probability
- personal fair probability
- edge in percentage points
- data sources and evidence cutoff
- active bankroll, planned risk, current event exposure, and risk caps
- exit plan
- post-mortem fields for outcome, notes, and mistake tags

## Phases

### Phase 1: Manual research system

Build the journal, schema validation, implied-probability math, edge filter,
and post-mortem workflow. Use paper trades or tiny live trades only.

### Phase 2: Data-backed probability models

Add event-specific research modules:

- CPI surprise model
- Fed decision probability model
- unemployment/payroll print model
- GDP or recession nowcast-style model

### Phase 3: Risk engine

Enforce active-bankroll sizing, max per-trade loss, max correlated exposure,
edge threshold, cooldowns, and drawdown rules.

### Phase 4: Performance dashboard

Measure the track record:

- realized P/L
- expected value versus actual
- Brier score
- calibration curve
- average edge captured
- mistakes by category
- performance after fees and spread
