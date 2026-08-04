# Broker reconciliation — the design to build before live money

**Status:** designed, NOT built. Deliberately deferred 2026-08-04.
**Blocked on:** nothing technical. Deferred because it has zero effect until
the engine trades real money, and the live gate needs 90 net-positive paper
sessions first (`te/risk/live_gate.py`). If the strategy work concludes there
is no edge, live mode never happens and this is never needed.

## What reconciliation is

Our engine keeps its own record of what it owns, rebuilt from an append-only
event log (`te/execution/store.py`). The broker keeps its own. Reconciliation
is the check that the two agree.

They can disagree for real reasons: a fill we never received, an order that
timed out mid-flight, a position squared off by the broker's own risk system,
or an order placed by hand in the broker's app.

## Why it is switched off today

`te/execution/reconcile.py` exists, is tested, and is **never scheduled**.
That is correct for now, and the reason is structural rather than an oversight.

`reconcile()` diffs local open positions against `BrokerPort.position_reports()`.
In paper mode the "broker" is a `SimulatedBroker` rebuilt fresh inside
`build_paper_execution_stack` (`te/execution/manager.py`) on every cycle, so it
always reports zero positions. Every genuinely open position would read as a
mismatch, and a mismatch halts by design — the engine would stop within a
minute of starting.

NautilusTrader draws the same line for the same reason: *"Only the
`LiveExecutionEngine` performs reconciliation, since backtesting controls both
sides."* Reconciliation only means anything against a system we do not control.

## What we have vs what is correct

| | Have | Correct |
|---|---|---|
| Startup reconciliation | **No** | **Yes — mandatory.** Runs once before any strategy starts |
| Continuous checks | Yes (unscheduled) | Yes, with a grace period |
| Response to mismatch | Always halt | Depends on whether it is explainable |
| Grace period before flagging | **No** | **Yes** — a new order must not be flagged just because the broker has not caught up |

### Startup reconciliation is the bigger gap

We only have the continuous kind, and the startup pass is the more valuable of
the two. The engine is off overnight. If anything changed the account while it
was down — a manual trade, a broker auto-square-off, an overnight assignment —
the engine wakes at 09:15 with a stale picture and starts trading against it.

The startup pass asks the broker "what do I actually own right now?" before
anything else runs.

### Halt vs auto-heal — not a single answer

Our current design halts on ANY disagreement. NautilusTrader does the opposite:
treats the venue as the source of truth, self-heals, and logs what it cannot
resolve — the argument being that the broker holds the binding position and our
copy is only a cache, so a network blip should not stop trading.

Neither blanket rule is right. Split by whether the difference is explainable:

| Situation | Response | Why |
|---|---|---|
| A fill we missed | **Auto-heal** | Broker is right, cause is understood, just record it |
| An in-flight order that timed out | **Auto-heal after grace period** | Resolve to its real terminal state |
| A position quantity we cannot account for | **Halt** | Trading around a position we do not understand is how small problems become large ones |
| An order we never placed | **Halt** | Could be the owner on their phone — or could be a bug placing real orders |

This is stricter than NautilusTrader and far less trigger-happy than what we
have now.

## To build, when the time comes

1. **Grace period** before any discrepancy is flagged (NautilusTrader's
   `open_check_threshold_ms`). This alone removes most false positives.
2. **Classify** discrepancies into auto-healable vs must-halt, per the table
   above, rather than the current blanket halt.
3. **Startup reconciliation**: a distinct pass, run once before strategies
   start, gated so continuous checks only begin after it completes.
4. **Schedule it in live mode only** — the same flag that enables live trading.
5. If paper-mode coverage of this code path is ever wanted, the prerequisite is
   making `SimulatedBroker` positions persist across cycles. Until then the
   check can only produce false halts, never find a real problem. Testing the
   reconciler via unit tests with a mocked broker report is the better route.

## Sources

- [NautilusTrader — Execution Reconciliation](https://nautilustrader.io/docs/latest/concepts/reconciliation)
  (config flags: `reconciliation`, `reconciliation_lookback_mins`,
  `open_check_interval_secs`, `open_check_threshold_ms`,
  `reconciliation_startup_delay_secs`)
- [NautilusTrader — Live Trading](https://nautilustrader.io/docs/latest/concepts/live/)
- [QuantConnect — Brokerages](https://www.quantconnect.com/docs/v2/writing-algorithms/live-trading/brokerages)
  (daily cash sync against the brokerage; their "Reconciliation" page is a
  different concept — live equity curve vs a parallel backtest, not order state)
