# Operations runbook

Read this once, calmly, before you ever run this engine against real money.
Bookmark it — it's written to be followed at 2am when something has already
gone wrong, not studied in advance. Every step below references a real file,
endpoint, or DB flag that exists in this codebase today (`engine/te/...`);
nothing here is aspirational. Where a piece of tooling the plan calls for
doesn't exist yet, that's stated explicitly rather than glossed over.

Environment reminder before you run any command below: `TE_ENV` and
`TE_DATABASE_URL` have no defaults (`te/settings.py`) — every command must be
run with them set explicitly, and **always confirm which one you're pointed
at** (`echo $TE_DATABASE_URL`) before touching a DB file. There is no dev/prod
switch in code, only the env var — the only thing standing between you and
running a command against prod by accident is reading the value before you
hit enter.

---

## (a) Halt drill — verify the kill switch actually stops new orders

The kill switch is layered (`te/risk/killswitch.py`):

1. **In-process flag** — fastest, but cold on every process restart.
2. **DB flag** — `engine_state` table, keys `halted`/`halt_reason`
   (`te/execution/halt.py`). This is the layer that survives a restart, and
   the one `ExecutionManager.submit()` (`te/execution/manager.py:72-73`)
   actually checks before submitting any order — a halted engine raises
   `HaltedError` and refuses to submit, it does not silently skip.
3. **Broker square-off** — manual, see section (b). Doesn't trust this
   codebase at all, which is the point.

**Known gap, be aware of it:** `POST /api/engine/pause` / `POST
/api/engine/resume` (`te/api/routers/engine.py`) currently only toggle an
in-memory dashboard display flag (`te.api.state.state.engine_health`) — they
do **not** set the real, persisted `engine_state.halted` flag that
`ExecutionManager.submit()` checks. Do not rely on the dashboard's
pause/resume button as a real kill switch until this is wired up (flagged
here so it isn't discovered live). The real, currently-working mechanisms are
steps 2 and 4 below.

### Drill steps

1. **Confirm the engine is actually running** paper cycles first (so the
   drill proves something) — check `GET /api/dashboard/snapshot`'s
   `nextCheckInSeconds` is counting down, or tail the structured log for
   recent `cycle_id` entries.

2. **Trip the DB-flag halt directly**, against the *same* DB the running
   engine process is pointed at (same `TE_DATABASE_URL`):

   ```bash
   cd engine
   TE_ENV=dev TE_DATABASE_URL=sqlite:///data/dev.db python -c "
   from te.persistence.db import engine_from_settings, make_session_factory
   from te.risk import killswitch
   from te.settings import Settings
   sf = make_session_factory(engine_from_settings(Settings()))
   with sf() as s:
       killswitch.trip(s, 'manual halt drill')
       s.commit()
   "
   ```

   (Substitute the real prod `TE_DATABASE_URL` when this drill is run against
   the live engine — never hardcode it into a script.)

3. **Watch the next cycle.** Any pending order submission must raise
   `HaltedError` in the log (`te/execution/manager.py`) and no new
   `order_events` rows should appear for it. Confirm via:

   ```bash
   sqlite3 data/dev.db "select event_type, ts from order_events order by id desc limit 5;"
   ```

   No new rows after the halt timestamp = pass.

4. **Confirm existing open positions are untouched but exits still work.**
   The halt blocks *new order submission*; it does not freeze
   `te/engine/exits.py`'s stop/trailing-stop/target/time-exit management on
   already-open positions (verify this is still the desired behaviour before
   relying on it — if in doubt, treat any halt as "go to step (b), square off
   manually" rather than assuming exits alone are sufficient).

5. **Clear the halt** once the drill is done, so the engine doesn't stay down
   for no reason:

   ```bash
   TE_ENV=dev TE_DATABASE_URL=sqlite:///data/dev.db python -c "
   from te.persistence.db import engine_from_settings, make_session_factory
   from te.execution.halt import clear_halt
   from te.settings import Settings
   sf = make_session_factory(engine_from_settings(Settings()))
   with sf() as s:
       clear_halt(s)
       s.commit()
   "
   killswitch.reset_in_process_cache()  # if the SAME process needs its in-process flag cleared too — usually easier to just restart the process
   ```

   In practice the simplest, safest way to clear an in-process trip is to
   restart the engine process — the DB flag is what's authoritative anyway.

6. **Pass criterion for the drill:** zero new order submissions between the
   trip (step 2) and the clear (step 5), and the halt flag/reason are visible
   in `engine_state` throughout — check with
   `sqlite3 data/dev.db "select * from engine_state where key like 'halt%';"`.

---

## (b) Manual square-off — engine stopped with open positions

If the engine process is being stopped (crash, deploy, deliberate shutdown)
while `open_positions` has rows (`select * from open_positions where
closed_at is null;`), the exit-management loop (`te/engine/exits.py`) stops
running too — nothing is watching stops/targets/time-exits anymore. Do not
assume it's safe to just leave positions open until the engine comes back.

1. **List what's actually open**, against the running DB:
   ```bash
   sqlite3 data/dev.db "select client_order_id, symbol, direction, lots, stop_paise, current_stop_paise, target_paise, hard_exit_by from open_positions where closed_at is null;"
   ```
2. **In dry-run/paper mode**: no real exposure exists at the broker — you can
   safely leave the paper position open and resume the engine later, or, if
   you want the books clean, mark it closed manually via the same repo
   function the engine itself uses (`te.persistence.repos.paper_trading.mark_position_closed`)
   rather than hand-editing the row — keeps the `trades` table's cost/PnL
   bookkeeping honest.
3. **In live mode**: this is real money. **Do not trust this engine's own API
   or dashboard for the square-off** — per `te/risk/killswitch.py`'s module
   docstring, the whole point of this layer is that it doesn't depend on the
   codebase being correct. Log into the **broker dashboard / OpenAlgo UI
   directly**:
   - For each row listed in step 1, place an opposite-side market order for
     the same `lots`/`symbol` directly in the broker UI to flatten the
     position.
   - Cancel any other open/pending orders for the account from the same UI.
   - Only after confirming (broker UI, not this engine) that the account is
     flat, reconcile the local DB: mark the corresponding `open_positions`
     rows closed and insert the matching `trades` rows with the real fill
     price you got at the broker, so the local P&L stays truthful. If you're
     not confident doing this by hand, leave the rows as-is and note the
     discrepancy — a stale-but-honestly-wrong local record beats a
     silently-fabricated "closed" row.
4. **Trip the halt** (section (a), step 2) before restarting the engine, so
   it doesn't immediately try to act on stale state — clear it only once
   you've confirmed reconciliation (`te/execution/reconcile.py`'s boot-time
   check) agrees with the broker.

---

## (c) DB restore procedure

`scripts/refresh_dev_db.py` (referenced in the project plan, `sqlite3
prod.db ".backup dev_tmp.db"` pattern) **has not been built yet** — this
section documents the plan's intended pattern directly, to be used by hand
until that script exists. Do not use plain `cp`/`copy` on a live SQLite file
under WAL — you can get a torn, inconsistent copy. Always use `.backup`.

### Taking a backup

```bash
sqlite3 data/prod.db ".backup data/backups/prod-$(date +%Y%m%dT%H%M%S).db"
```
`.backup` is WAL-consistent — it takes a point-in-time snapshot correctly even
while the engine is writing. Do this on a schedule (cron/scheduled task), not
only before risky operations.

### Restoring from a backup

1. **Stop the engine process first.** Restoring into a file a live process
   has open invites corruption.
2. Confirm which DB you're about to overwrite:
   ```bash
   echo $TE_DATABASE_URL   # must match the file path below
   ```
3. Move the current (possibly bad) file aside rather than deleting it —
   you may need it for forensics:
   ```bash
   mv data/prod.db data/prod.db.bad-$(date +%Y%m%dT%H%M%S)
   ```
4. Restore the chosen backup into place:
   ```bash
   sqlite3 data/backups/prod-<timestamp>.db ".backup data/prod.db"
   ```
5. Sanity-check before restarting the engine:
   ```bash
   sqlite3 data/prod.db "select key, value, updated_at from engine_state;"
   sqlite3 data/prod.db "select count(*) from trades;"
   sqlite3 data/prod.db "select count(*) from open_positions where closed_at is null;"
   ```
   Cross-check the open-positions count against what the broker UI actually
   shows before trusting it — a restored DB describes the past, not
   necessarily what's true at the broker right now (see section (b) if they
   disagree).
6. Only restart the engine once you're confident the restored DB and the
   broker's actual state agree — `te/execution/reconcile.py`'s boot check
   will halt on disagreement rather than auto-heal, so a mismatch here should
   surface loudly on the next boot rather than trade silently.

**Dev refresh** (pulling prod data into dev safely) follows the same
`.backup` pattern the plan specifies for `scripts/refresh_dev_db.py`:
`sqlite3 prod.db ".backup dev_tmp.db"`, then scrub credentials/secrets from
the copy, then move it into place as `dev.db` — **never run this with
`TE_ENV=prod`** (the eventual script should refuse to run under that env, per
the plan; until it exists, check `TE_ENV` by hand every time before doing
this manually).

---

## (d) Non-code prerequisites for going live — none of this is code

The live-money unlock gate (`te/risk/live_gate.py`'s `LiveUnlockGate`, wired
into `POST /api/mode {mode:'live'}`) checks the *trading-evidence* side of
readiness (DSR, PBO, post-freeze paper sessions, slippage, ML maturity
stage) and will return **HTTP 409** with the specific unmet conditions until
all of them hold. Passing that gate is necessary but **not sufficient** —
none of the following is checked by code, and all of it must be done by the
user directly before live money is legally/operationally safe to enable:

1. **Mumbai VPS with a static IP.** Dev currently runs on a Windows box with
   no static IP — that's fine for paper trading against live data (no
   broker whitelisting needed for market data), but **live order placement
   requires a static IP the broker has whitelisted** (see point 2). Do not
   discover this requirement for the first time at go-live (the project plan
   flags this explicitly as risk R11) — provision the VPS well before you
   expect to flip the switch, and run paper trading from it for a while
   first to shake out latency/networking issues on the new box.

2. **Register that static IP with the broker (Angel One / OpenAlgo).** The
   API key is currently scoped to whatever IP(s) the broker has on file;
   live order placement from an unregistered IP will simply fail at the
   broker, not at this engine. Do this from the broker's API-management
   console, using the VPS's static IP from point 1. Re-verify after any VPS
   IP change (e.g. a VPS rebuild).

3. **SEBI Algo-ID registration**, per the framework at `SEBI/HO/MIRSD/
   MIRSD-PoD/P/CIR/2025/13`, fully mandatory from **1 Apr 2026**. This
   project's stated order-rate cap (`te/broker/ratelimit.py`, 5 orders/sec,
   already under the SEBI **<10 orders/sec** personal-use threshold) means
   formal registration is *not* required at this project's current scale —
   but confirm this against the live circular text yourself before relying
   on it; regulatory thresholds and framework wording can change, and this
   runbook is not a substitute for reading the circular. If the trading
   pattern ever needs to exceed 10 orders/sec, registration becomes
   mandatory and must be completed *before* that rate is used, not
   retroactively.

4. **Re-run the halt drill (section a) and a manual square-off dry run
   (section b) on the actual VPS**, against the actual broker connection,
   before the first real-money session — a drill that only ever ran against
   a paper/simulated venue on a dev laptop has not actually proven the
   production path works.

None of items 1-3 can be completed by an agent working in this sandbox; they
require a live VPS provider account, a login to the broker's API console,
and (for item 3, if it becomes applicable) a SEBI-facing registration
process. This runbook exists so a human can execute them directly.
