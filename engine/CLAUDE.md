# Trading engine — project instructions

Personal algorithmic trading engine for Indian index options (NIFTY, BANKNIFTY,
SENSEX, BANKEX) on a real ~Rs 50,000 account. FastAPI + SQLAlchemy 2 +
pydantic-settings, talking to a live broker (Angel One) through a self-hosted
OpenAlgo gateway.

Today it runs in paper mode against live market data on a 1-minute cycle:
read bars -> evaluate an entry rule -> size against a rupee risk budget -> buy
an option -> manage stop/target/trailing exits until it closes.

**A wrong number is worse than a crash.** A crash is visible. A wrong number is
a plausible result nobody questions, and it reaches a real account.

## The dominant bug class

> The value is present, the type is correct, and the MEANING is wrong.

Confirmed instances in this codebase, all shipped, all passing their tests:

- a tick timestamp in **milliseconds parsed as nanoseconds** — silently wrote
  zero bars for days
- a broker freeze-quantity of `1` meaning "value missing", read as "cap at 1 unit"
- an open position's **entry price returned as its "current price"** — silently
  disabled every price-based exit
- `max_loss_paise` documented as a net loss cap, actually enforced as a **gross
  price distance with costs excluded**
- risk counters reading **CLOSED** trades and calling the result "trades today",
  so live positions were invisible to three separate guards. This cost a real
  session: 6 trades against a cap of 3, 6 straight losses against a stand-down
  of 3, Rs 5,612 lost against a Rs 2,500 daily cap.
- a strategy whose entry time sat outside the tradeable window, so it never
  fired once and nobody noticed

Nothing is malformed in any of those. No type checker, schema, or linter catches
them. **When you find one instance, sweep every analogous function for the same
mistake** — the counter bug was found and fixed once in `check_daily_loss_limit`
and nobody checked its two neighbours in the same file.

## Money arithmetic

- Money is **integer paise** everywhere. Never floats.
- Options trade in lots. `quantity = lots * lot_size`. Lot sizes are read from
  the broker at runtime, never hardcoded (SEBI revises them ~every six months).
  Nearly every money bug here lives in that multiplication.
- `net == gross - costs`. Any P&L-shaped value must be net, computed through
  `te/domain/costs.py`'s `CostModel` with dated rates from `config/charges.yaml`.
- Re-derive money math from scratch rather than checking it against its comment.
  If a value is described as a rupee cap, confirm it caps rupees **at the
  position size it will really be used with**.
- Watch integer-division truncation and rounding direction.

## Two ledgers, and why counters get this wrong

- `open_positions` = **entry** ledger. Row written at open, never deleted,
  `closed_at` stamped on close.
- `trades` = **exit** ledger. Row written only on close.
- Joined on `client_order_id`.

Anything counting "today's activity" must count `open_positions` **plus** trades
closed today. A counter that reads only `trades` is blind to every live
position. That is the exact shape of the breach above.

## Dates: rows are UTC, the trading day is IST

Every place that reasons about "today" must convert. Known latent bug:
`te/persistence/repos/paper_trading.py::_day_bounds` attaches UTC tzinfo to an
IST date and closes its interval at `time.max`. It cannot bite today only
because NSE/BSE hours never straddle UTC midnight. Treat any other "today"
computation with the same suspicion.

`>` vs `>=` on a cap decides whether a compliant day reports a false violation
or a real breach passes silently. Check the boundary on every limit.

## Comments and docstrings are unverified claims

This codebase has long, confident, detailed prose and some of it is flatly
wrong. One module docstring confidently explained why a broker relogin was
"deliberately NOT retried" — that reasoning was wrong and cost a morning of dead
quotes. Where comment and code disagree, that is a finding.

## Tests are suspect too

There are ~1,559 passing tests and the engine still broke four of its own risk
limits in one live session. The tests were written by whoever held the wrong
idea, so they agree with the bug and pass. Every test for the broken counters
inserted a **closed** trade — precisely the case the buggy code handled
correctly.

For any test, ask: *would this still pass if the code were wrong in the obvious
way?* Does the fixture set up the case the code gets RIGHT, or the case it gets
WRONG? A test that cannot fail is worse than no test — it manufactures
confidence. Flag it.

Note for anyone writing tests: this project renders logs through structlog's own
pipeline, so pytest's `caplog` reads **empty**. Use
`structlog.testing.capture_logs()`.

## Wired but not connected

This project has repeatedly shipped code that runs but influences nothing:
monitors constructed and never called, an ML hook never passed to the cycle, a
dashboard toggle no engine code reads, a "slippage is clean" gate that passes
trivially on zero observations. **A check that cannot fail is not a check.**

## Silent failures

Broad `except Exception`, fallbacks to a stale or default value, anything that
swallows an error and lets the caller believe it succeeded. Background threads
matter most — a full session of bar recording was lost to a background task that
died quietly while looking alive.

## Point-in-time correctness

`te/data/asof.py`'s `bars_asof` is the **only** sanctioned way to read bars. It
filters on `close_ts <= as_of AND ingested_at <= as_of`. Both halves matter: the
gate on close (not open) stops a strategy seeing an unfinished bar; the
`ingested_at` half stops a backfilled or revised bar leaking into a decision
that happened before that data existed. A direct `BarStore` read outside
`te/data/asof.py` is a bug.

## Not worth flagging

- Formatting, naming, import order — CI handles it.
- Strategy alpha. The entry rule has been measured and scores the same as random
  entry before costs. Suggestions for better strategies are out of scope; the
  question is whether the machine is CORRECT.
- Type errors a checker would catch.
