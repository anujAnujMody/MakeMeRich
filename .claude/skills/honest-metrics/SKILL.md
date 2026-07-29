---
name: honest-metrics
description: Rules for reporting any performance number (Sharpe, win rate, accuracy, P&L) in this trading engine — DSR/PBO thresholds, the trial ledger, banned fabrications, and net-vs-gross discipline. Use when computing, displaying, or reviewing any metric under engine/te/ml, engine/te/backtest, or any dashboard-facing endpoint.
---

# Honest metrics

This project exists specifically because a previous version of this engine reported fabricated
numbers as real (in-sample "75% accuracy" displayed as live performance, a Sharpe of 3.85 from a model
that had never traded a single option). Every rule below closes one specific way that happens again.

## The trial ledger is the whole point

`engine/te/ml/trials.py`'s `TrialLedger` records **every** backtest run, CV fold, Optuna trial, and
parameter sweep — permanently. It has **no delete method anywhere in the codebase**, and the
`trial_ledger` SQLite table has a `BEFORE DELETE` trigger that raises even on a hand-run `DELETE FROM
trial_ledger`. This is deliberate: Deflated Sharpe Ratio's whole mechanism is "the more things you
tried, the higher the bar for the one that looks good" — and the only way that protection can be
subverted is by quietly forgetting how many things were tried. **Never** add a way to reset or trim
this table, even for "cleanup." If it feels inconvenient that N keeps climbing, that inconvenience is
the feature.

## DSR and PBO — the actual gates

`engine/te/ml/metrics.py` implements Probabilistic Sharpe Ratio, Deflated Sharpe Ratio, and PBO via
CSCV, transcribed from Bailey & López de Prado. The thresholds that matter everywhere in this project:

- **DSR > 0.95** — required before a strategy/model may be treated as having a real, non-lucky edge.
- **PBO < 0.05** — required before a parameter choice may be treated as generalizing rather than
  curve-fit.

A DSR/PBO run that fails these thresholds is a **successful, correct outcome**, not a bug to route
around by lowering the bar, cherry-picking the trial set, or "just running it again." If a number
doesn't clear the gate, the honest thing to display is that it didn't clear the gate.

## Net, never gross

`engine/te/domain/pnl.py`'s `NetPnl` is a distinct type from `GrossPnl`; `net_pnl()` is the only way
to construct one. Every P&L-shaped API response field, every dashboard metric, every backtest report
number must be `NetPnl` — computed via `engine/te/domain/costs.py`'s `CostModel`, which itself needs
real, dated charge rates from `engine/config/charges.yaml` (never a guessed or stale rate). If you ever
see a bare `pnl` field or a P&L number computed without routing through `CostModel`, that's gross P&L
leaking through, and it will overstate returns by however much brokerage/STT/GST/slippage costs.

## Banned fabrications — the specific list

These are not hypothetical; each one is a documented failure mode from the plan's research or the
prior engine:

- `winRate`/similar metrics when `totalTrades == 0` — return `0`, never a plausible-looking default
  like `50`.
- `sharpe`/`profitFactor` when the sample size is too small to mean anything (n < 100 is this
  project's floor) — omit the field entirely rather than compute a number nobody should trust.
- `confidence` on a strategy/pattern card without a real backing sample.
- Any endpoint returning data before its owning phase actually produces it — return the type's honest
  zero-state (`[]`, `0`, `null`) with the `X-TE-Provenance`/`X-TE-Sample-Size` headers, never a
  plausible-looking placeholder.
- In-sample metrics displayed without an explicit "in-sample, not tradeable" distinction from
  out-of-sample ones.

## The ML maturity gate is a call-graph guarantee, not a convention

`engine/te/ml/gates.py`'s `MaturityGate.influence(p)` is the **only** thing `engine/te/engine/cycle.py`
is allowed to receive from `te.ml` — `cycle.py` does not import the model itself and never sees a raw
probability. Below `gating` stage, `MLInfluence` is always `(size_multiplier=1, veto=False,
displayed_verdict=None)`, so the ML layer is provably inert. If you're tempted to let a component read
a raw model score directly instead of going through `MaturityGate`, don't — that reintroduces exactly
the "the model quietly started influencing trades before it earned the right to" failure mode.

## Before displaying any number, ask

1. Is this net of real, dated costs — or could it be gross?
2. Is the sample size large enough to be meaningful, and is it shown alongside the number (`n=...`)?
3. If this came from a backtest/CV run, did it go through the trial ledger, and does the reported
   DSR/PBO reflect the honest trial count?
4. Would this number still be displayed if the underlying result were bad? (If the code path only
   exists for the good-outcome case, that's a tell.)
