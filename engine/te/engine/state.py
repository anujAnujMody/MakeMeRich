"""Persisted `mode`/`run_state` — extends Phase 0's `engine_state`
key/value table (same one `te.execution.halt` already uses for the halt
flag) rather than adding a new table, so a restart can never silently reset
either back to a default."""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass, replace
from decimal import Decimal, InvalidOperation
from typing import Literal, get_args

from sqlalchemy.orm import Session

from te.domain.clock import to_utc
from te.domain.money import Paise
from te.domain.symbols import FNO_UNDERLYING_EXCHANGES
from te.persistence.models import EngineState
from te.persistence.repos.instruments import latest_lot_size
from te.settings import Settings

Mode = Literal["dry-run", "live"]
RunState = Literal["running", "paused"]

_MODE_KEY = "mode"
_RUN_STATE_KEY = "run_state"
_PARAMS_FROZEN_AT_KEY = "params_frozen_at"

_DEFAULT_MODE: Mode = "dry-run"
_DEFAULT_RUN_STATE: RunState = "paused"

# --- Account guardrails: live-editable capital/risk config, read fresh on
# every paper-cycle run (see PaperCycleRunner.run_once) instead of frozen
# once at process startup from Settings.paper_cycle_* env vars. Same
# key/value table, same "setter validates + never commits" convention as
# mode/run_state above. ---

_CAPITAL_PAISE_KEY = "capital_paise"
_MAX_DAILY_LOSS_PAISE_KEY = "max_daily_loss_paise"
_MAX_POSITION_SIZE_PCT_KEY = "max_position_size_pct"
_MAX_DRAWDOWN_PCT_KEY = "max_drawdown_pct"
_MAX_TRADES_PER_DAY_KEY = "max_trades_per_day"
_MAX_CONCURRENT_POSITIONS_KEY = "max_concurrent_positions"
_RISK_PER_TRADE_PCT_KEY = "risk_per_trade_pct"
#: When `capital_paise` was last CHANGED (not merely re-saved). This is the
#: anchor the sizing/drawdown equity counts realized P&L from — see
#: `get_capital_set_at`.
_CAPITAL_SET_AT_KEY = "capital_set_at"


@dataclass(frozen=True)
class AccountGuardrails:
    capital: Paise
    max_daily_loss: Paise
    max_position_size_pct: Decimal
    max_drawdown_pct: Decimal
    max_trades_per_day: int
    max_concurrent_positions: int
    risk_per_trade_pct: Decimal


# --- Hard ceilings on the dashboard-editable risk fields ---------------
#
# These are the ONLY guardrail bounds that are not operator-editable. The
# Settings page validates nothing meaningful on its own (a `(0, 100]` range
# check accepts 100% drawdown, which is the same as no limit at all), and
# the values actually in the live DB on 2026-07-31 were 5% risk per trade,
# a Rs 45,000 daily loss limit on Rs 3,00,000 capital (15%), and both the
# drawdown and position-notional caps set to 100% — i.e. disabled.
#
# Why these numbers: risk-of-ruin. At 5% risked per trade, a 10-loss streak
# — which is an ordinary occurrence inside ~250 trades at a ~50% win rate —
# is a ~40% account drawdown. At 1.5% the same streak costs under 15%.
#
# RAISED 2026-08-05, deliberately, by the owner — and the arithmetic above
# is unchanged and still true. The reason is affordability, measured, not
# preference: on the real Rs 30,000 account one NIFTY lot at a ~Rs 42
# premium risks ~Rs 549 at a 20% stop, while 1.5% of capital is Rs 450. At
# the old ceiling `size_position` rejected EVERY signal for want of Rs 99 —
# verified by re-sizing 2026-08-04's four real trades, all four of which
# came back `lots=0`. A ceiling that permits no trade at all does not
# protect the account, it just makes the engine idle while looking healthy.
#
# So the band below is now the SMALL-ACCOUNT band, not the prop-desk one,
# and it is a knowingly accepted trade: 5% per trade means a 10-loss streak
# costs ~40% of Rs 30,000. `MAX_DAILY_LOSS_PCT_OF_CAPITAL` is what actually
# bounds that in practice — at Rs 2,000/day the account stands down after
# roughly one full stop-out, long before any streak can compound. Revisit
# both together, never one alone: dropping the daily limit back to 5%
# (Rs 1,500) while leaving risk at 5% means the first loser ends every day.
#
# Enforced in BOTH directions on purpose:
#   * `set_guardrails` REJECTS a save above a ceiling, with the ceiling named
#     in the error, so an operator is told rather than silently overruled.
#   * `get_guardrails` CLAMPS on read, so values already stored above a
#     ceiling (all four of the ones above were) stop being honoured
#     immediately, without needing a migration or a manual re-save. Clamping
#     is the safe direction: the failure mode is trading smaller than asked,
#     never larger. The clamped values are what `GET /api/engine/guardrails`
#     returns, so the dashboard shows what is genuinely in force.
MAX_RISK_PER_TRADE_PCT = Decimal(5)
#: 7, not 5, so the owner's chosen Rs 2,000/day fits on Rs 30,000 (6.67%)
#: with no headroom to spare — a deliberately tight ceiling rather than a
#: round number that would quietly permit Rs 3,000.
MAX_DAILY_LOSS_PCT_OF_CAPITAL = Decimal(7)
MAX_DRAWDOWN_PCT_CEILING = Decimal(20)
#: 50, raised from 25 on 2026-08-05 by the owner, for the same affordability
#: reason as the risk ceiling above and measured the same morning: one
#: next-weekly NIFTY ATM lot costs ~Rs 9,919, and 25% of Rs 30,000 is
#: Rs 7,500 — so the cap alone rejected every non-expiry-day signal even
#: after the stop was tightened. Both constraints had to move; fixing either
#: one on its own changed nothing.
#:
#: The cost is real and is not hidden: at 50% a single position can hold half
#: the account. On Rs 30,000 that is what buying ONE index-option lot means —
#: the lot is indivisible, so the alternative is not a smaller position, it is
#: no position at all.
MAX_POSITION_SIZE_PCT_CEILING = Decimal(50)


def _daily_loss_ceiling(capital: Paise) -> Paise:
    return Paise(int(Decimal(int(capital)) * MAX_DAILY_LOSS_PCT_OF_CAPITAL / Decimal(100)))


def clamp_guardrails(guardrails: AccountGuardrails) -> AccountGuardrails:
    """Lower any field that sits above its hard ceiling. Pure — no session,
    no I/O — so it is equally usable on a value read from the DB, one parsed
    off an API request, or one built from `Settings` defaults."""
    return replace(
        guardrails,
        max_daily_loss=Paise(min(int(guardrails.max_daily_loss), int(_daily_loss_ceiling(guardrails.capital)))),
        max_position_size_pct=min(guardrails.max_position_size_pct, MAX_POSITION_SIZE_PCT_CEILING),
        max_drawdown_pct=min(guardrails.max_drawdown_pct, MAX_DRAWDOWN_PCT_CEILING),
        risk_per_trade_pct=min(guardrails.risk_per_trade_pct, MAX_RISK_PER_TRADE_PCT),
    )


def upsert_engine_state(session: Session, key: str, value: str) -> None:
    """The ONE writer for the `engine_state` key/value table — shared with
    `te.execution.halt`, which stores its halt/throttle flags as keys in this
    same table (see that module's docstring for why it reuses this table
    rather than adding its own)."""
    row = session.get(EngineState, key)
    if row is None:
        session.add(EngineState(key=key, value=value))
    else:
        row.value = value
        row.updated_at = dt.datetime.now(dt.UTC)


def get_mode(session: Session) -> Mode:
    row = session.get(EngineState, _MODE_KEY)
    if row is None or row.value not in get_args(Mode):
        return _DEFAULT_MODE
    return row.value  # type: ignore[return-value]


def set_mode(session: Session, mode: Mode) -> None:
    """Does not commit — callers own the transaction boundary, matching
    `te.execution.halt`'s existing convention."""
    if mode not in get_args(Mode):
        raise ValueError(f"unknown mode {mode!r} — expected one of {get_args(Mode)}")
    upsert_engine_state(session, _MODE_KEY, mode)


def get_run_state(session: Session) -> RunState:
    row = session.get(EngineState, _RUN_STATE_KEY)
    if row is None or row.value not in get_args(RunState):
        return _DEFAULT_RUN_STATE
    return row.value  # type: ignore[return-value]


def set_run_state(session: Session, run_state: RunState) -> None:
    if run_state not in get_args(RunState):
        raise ValueError(f"unknown run_state {run_state!r} — expected one of {get_args(RunState)}")
    upsert_engine_state(session, _RUN_STATE_KEY, run_state)


def get_params_frozen_at(session: Session) -> dt.datetime | None:
    """When the active strategy's tunable parameters were last frozen —
    `None` means never frozen (or reset on the most recent param change, per
    the plan's R5). This is the anchor `te.risk.live_gate.LiveUnlockGate`
    counts post-freeze paper sessions from: "paper is OOS" only holds after
    this timestamp."""
    row = session.get(EngineState, _PARAMS_FROZEN_AT_KEY)
    if row is None or not row.value:
        return None
    return dt.datetime.fromisoformat(row.value)


def set_params_frozen_at(session: Session, ts: dt.datetime) -> None:
    """Resets on any param change (per the plan's R5) — callers own calling
    this again whenever tunable parameters are edited, not just on the
    first freeze."""
    upsert_engine_state(session, _PARAMS_FROZEN_AT_KEY, to_utc(ts, name="params_frozen_at").isoformat())


def _read_int(session: Session, key: str, default: int) -> int:
    row = session.get(EngineState, key)
    if row is None:
        return default
    try:
        return int(row.value)
    except ValueError:
        return default


def _read_decimal(session: Session, key: str, default: Decimal) -> Decimal:
    row = session.get(EngineState, key)
    if row is None:
        return default
    try:
        return Decimal(row.value)
    except InvalidOperation:
        return default


_LAST_BROKER_RELOGIN_DAY_KEY = "last_broker_relogin_day"


def get_last_broker_relogin_day(session: Session) -> dt.date | None:
    """The IST date of the last SUCCESSFUL Angel/OpenAlgo relogin, or `None`
    if there has never been one.

    Exists so a mid-morning engine restart can tell "today's login already
    happened" from "today's login was missed", which is the difference
    between a harmless no-op and a session where every quote returns HTTP
    500. A cron trigger cannot answer that: `BackgroundScheduler` has no
    memory across processes, so after a restart it neither knows the job
    already ran nor that it did not.

    A DATE, not a timestamp. The broker session expires nightly, so "has
    today's login happened" is the only question worth asking, and storing a
    time would invite a staleness rule nobody has measured.

    Unparseable values read as `None` — the same lenient convention as the
    other readers here. The cost of re-running a login is one extra request;
    the cost of skipping one is the whole trading day.
    """
    row = session.get(EngineState, _LAST_BROKER_RELOGIN_DAY_KEY)
    if row is None or not row.value:
        return None
    try:
        return dt.date.fromisoformat(row.value)
    except ValueError:
        return None


def set_last_broker_relogin_day(session: Session, day: dt.date) -> None:
    upsert_engine_state(session, _LAST_BROKER_RELOGIN_DAY_KEY, day.isoformat())


_PEAK_EQUITY_PAISE_KEY = "peak_equity_paise"


def get_peak_equity_paise(session: Session) -> Paise | None:
    """The ratcheting peak-equity watermark `te.risk.limits.
    check_max_drawdown` compares current equity against. `None` (not `0`)
    when nothing has been recorded yet or the stored value fails to parse —
    same lenient-read convention as `_read_int`/`_read_decimal` above."""
    row = session.get(EngineState, _PEAK_EQUITY_PAISE_KEY)
    if row is None:
        return None
    try:
        return Paise(int(row.value))
    except ValueError:
        return None


def set_peak_equity_paise(session: Session, value: Paise) -> None:
    upsert_engine_state(session, _PEAK_EQUITY_PAISE_KEY, str(int(value)))


def clear_peak_equity_paise(session: Session) -> None:
    """Forget the watermark, so `check_max_drawdown` re-seeds it from real
    equity on the next cycle.

    Deletes the row rather than writing a value, because ONLY the trading
    loop can compute equity correctly: it is `capital + realized-since-anchor
    + unrealized`, and `unrealized` needs a `BarStore` and a `CostModel` that
    this module sits below in the layer rule. Any value written from here is
    a guess about open positions.

    That guess was wrong. Writing `capital` assumed equity equalled capital
    right after a re-base — true only with a flat book. With a position open
    and Rs 10,000 underwater, equity is Rs 40,000 against a watermark of
    Rs 50,000: an instant 20% drawdown, a halt, and a manual clear, all from
    a config edit that lost nothing. `check_max_drawdown` already handles
    `None` by seeding from whatever equity actually is, so deleting hands the
    decision to the one caller that can make it."""
    row = session.get(EngineState, _PEAK_EQUITY_PAISE_KEY)
    if row is not None:
        session.delete(row)


def get_capital_set_at(session: Session) -> dt.datetime | None:
    """When the account's capital was last CHANGED, or `None` if it has
    never been changed from the `Settings` default.

    This exists because account equity is `capital + realized P&L`, and
    without an anchor "realized P&L" means *lifetime* P&L — including
    trades taken back when capital was a different number. Raising capital
    from Rs 30,000 to Rs 50,000 on 2026-08-05 with Rs 11,837 of older paper
    profit on the books would have sized the very next trade off Rs 61,837,
    a balance that never existed under either setting.

    Re-saving the SAME capital deliberately does not move this anchor: an
    operator changing the daily-loss limit is not restating what the account
    is worth, and treating it as such would silently discard the running
    P&L that the drawdown breaker depends on.

    `None` means "never changed", and ONLY that. An unreadable stored value
    RAISES instead of returning `None`, because the two are opposites in
    effect: `None` restores lifetime P&L, which is exactly the bug this
    field exists to prevent, and it would do so silently on an account whose
    watermark was cleared for a re-base that then did not take effect. A
    tz-naive value is rejected here for the same reason — it parses fine but
    `to_utc` raises later, inside the entry cycle, aborting every instrument
    with a traceback that names the clock rather than this row."""
    row = session.get(EngineState, _CAPITAL_SET_AT_KEY)
    if row is None or not row.value:
        return None
    try:
        parsed = dt.datetime.fromisoformat(row.value)
    except ValueError as exc:
        raise ValueError(
            f"engine_state[{_CAPITAL_SET_AT_KEY}] is not a valid ISO timestamp: {row.value!r}. "
            "Position sizing cannot be trusted until this is corrected — equity would silently "
            "revert to lifetime P&L across a capital change."
        ) from exc
    if parsed.tzinfo is None:
        raise ValueError(
            f"engine_state[{_CAPITAL_SET_AT_KEY}] is tz-naive ({row.value!r}); it is written as UTC "
            "and must stay tz-aware."
        )
    return parsed


def get_guardrails(session: Session, *, defaults: AccountGuardrails) -> AccountGuardrails:
    """Reads each of the 7 guardrail keys independently, falling back to the
    corresponding field on `defaults` for any key that is missing or fails
    to parse — never a single all-or-nothing fallback, so a partially-saved
    guardrails row (or a single corrupted key) can't take the rest down with
    it. `defaults` is normally `Settings.paper_cycle_*`-derived (see
    `te.engine.scheduler._default_cycle_config`), so an operator who never
    touches the Settings page gets exactly today's env-var behaviour,
    unchanged.

    The result is passed through `clamp_guardrails` before being returned,
    so a stored value above a hard ceiling cannot be honoured by anything
    downstream — including rows written before those ceilings existed."""
    return clamp_guardrails(
        AccountGuardrails(
            capital=Paise(_read_int(session, _CAPITAL_PAISE_KEY, int(defaults.capital))),
            max_daily_loss=Paise(_read_int(session, _MAX_DAILY_LOSS_PAISE_KEY, int(defaults.max_daily_loss))),
            max_position_size_pct=_read_decimal(
                session, _MAX_POSITION_SIZE_PCT_KEY, defaults.max_position_size_pct
            ),
            max_drawdown_pct=_read_decimal(session, _MAX_DRAWDOWN_PCT_KEY, defaults.max_drawdown_pct),
            max_trades_per_day=_read_int(session, _MAX_TRADES_PER_DAY_KEY, defaults.max_trades_per_day),
            max_concurrent_positions=_read_int(
                session, _MAX_CONCURRENT_POSITIONS_KEY, defaults.max_concurrent_positions
            ),
            risk_per_trade_pct=_read_decimal(session, _RISK_PER_TRADE_PCT_KEY, defaults.risk_per_trade_pct),
        )
    )


#: `get_guardrails_provenance`'s per-field result: `"stored"` means the row
#: in `engine_state` exists and parsed, so it is what actually governs
#: trading; `"seed"` means that row is missing or unreadable and
#: `guardrails_defaults_from_settings` supplied the value instead — the trap
#: this whole module exists to make visible (see `te.engine.scheduler.
#: _warn_if_stored_guardrails_diverge_from_settings`).
GuardrailProvenance = Literal["stored", "seed"]

_GUARDRAIL_KEYS: tuple[str, ...] = (
    _CAPITAL_PAISE_KEY,
    _MAX_DAILY_LOSS_PAISE_KEY,
    _MAX_POSITION_SIZE_PCT_KEY,
    _MAX_DRAWDOWN_PCT_KEY,
    _MAX_TRADES_PER_DAY_KEY,
    _MAX_CONCURRENT_POSITIONS_KEY,
    _RISK_PER_TRADE_PCT_KEY,
)

#: `AccountGuardrails` field name, keyed identically to `_GUARDRAIL_KEYS`
#: above (same order) — the mapping `get_guardrails_provenance` reports
#: against, and what `GET /api/engine/guardrails` exposes per field.
_GUARDRAIL_FIELD_NAMES: tuple[str, ...] = (
    "capital",
    "max_daily_loss",
    "max_position_size_pct",
    "max_drawdown_pct",
    "max_trades_per_day",
    "max_concurrent_positions",
    "risk_per_trade_pct",
)

_GUARDRAIL_INT_FIELDS = frozenset({"capital", "max_daily_loss", "max_trades_per_day", "max_concurrent_positions"})


def get_guardrails_provenance(session: Session) -> dict[str, GuardrailProvenance]:
    """Per-field `"stored"`/`"seed"` — whether each of the 7 guardrails is
    actually the DB-stored value `get_guardrails` will return, or a
    `Settings.paper_cycle_*`-seeded fallback because no row (or an
    unparseable one) exists yet. Uses the exact same parse rules `_read_int`/
    `_read_decimal` apply, so this can never disagree with what
    `get_guardrails` actually returned for the same session."""
    provenance: dict[str, GuardrailProvenance] = {}
    for key, field_name in zip(_GUARDRAIL_KEYS, _GUARDRAIL_FIELD_NAMES, strict=True):
        row = session.get(EngineState, key)
        if row is None:
            provenance[field_name] = "seed"
            continue
        try:
            if field_name in _GUARDRAIL_INT_FIELDS:
                int(row.value)
            else:
                Decimal(row.value)
        except (ValueError, InvalidOperation):
            provenance[field_name] = "seed"
            continue
        provenance[field_name] = "stored"
    return provenance


def set_guardrails(session: Session, guardrails: AccountGuardrails) -> None:
    """Validates before writing ANY key (all-or-nothing on the write side,
    unlike the lenient per-key read above) — a partially-invalid save should
    never leave some keys updated and others stale. Does not commit —
    callers own the transaction boundary, matching every other setter in
    this module."""
    if guardrails.capital <= 0:
        raise ValueError(f"capital must be positive, got {guardrails.capital}")
    if guardrails.max_daily_loss < 0:
        raise ValueError(f"max_daily_loss must not be negative, got {guardrails.max_daily_loss}")
    if not (Decimal(0) < guardrails.max_position_size_pct <= Decimal(100)):
        raise ValueError(f"max_position_size_pct must be in (0, 100], got {guardrails.max_position_size_pct}")
    if not (Decimal(0) < guardrails.max_drawdown_pct <= Decimal(100)):
        raise ValueError(f"max_drawdown_pct must be in (0, 100], got {guardrails.max_drawdown_pct}")
    if guardrails.max_trades_per_day <= 0:
        raise ValueError(f"max_trades_per_day must be positive, got {guardrails.max_trades_per_day}")
    if guardrails.max_concurrent_positions <= 0:
        raise ValueError(f"max_concurrent_positions must be positive, got {guardrails.max_concurrent_positions}")
    if not (Decimal(0) < guardrails.risk_per_trade_pct <= Decimal(100)):
        raise ValueError(f"risk_per_trade_pct must be in (0, 100], got {guardrails.risk_per_trade_pct}")

    # Hard ceilings — see the constants' comment. Rejected rather than
    # silently clamped on the WRITE path so the operator is told the limit
    # exists instead of watching a saved value change under them.
    if guardrails.risk_per_trade_pct > MAX_RISK_PER_TRADE_PCT:
        raise ValueError(
            f"risk_per_trade_pct {guardrails.risk_per_trade_pct}% exceeds the hard ceiling of "
            f"{MAX_RISK_PER_TRADE_PCT}% — at {MAX_RISK_PER_TRADE_PCT}% a 10-loss streak is already "
            f"a ~40% account drawdown"
        )
    if guardrails.max_position_size_pct > MAX_POSITION_SIZE_PCT_CEILING:
        raise ValueError(
            f"max_position_size_pct {guardrails.max_position_size_pct}% exceeds the hard ceiling of "
            f"{MAX_POSITION_SIZE_PCT_CEILING}%"
        )
    if guardrails.max_drawdown_pct > MAX_DRAWDOWN_PCT_CEILING:
        raise ValueError(
            f"max_drawdown_pct {guardrails.max_drawdown_pct}% exceeds the hard ceiling of "
            f"{MAX_DRAWDOWN_PCT_CEILING}% — 100% means the breaker never trips"
        )
    ceiling = _daily_loss_ceiling(guardrails.capital)
    if guardrails.max_daily_loss > ceiling:
        raise ValueError(
            f"max_daily_loss {guardrails.max_daily_loss}p exceeds {MAX_DAILY_LOSS_PCT_OF_CAPITAL}% of "
            f"capital ({ceiling}p)"
        )

    # A CHANGE to capital re-bases the account, and two stored numbers
    # depend on that base:
    #
    # 1. `_CAPITAL_SET_AT_KEY` — the anchor equity counts realized P&L
    #    from. Without it, `capital + lifetime P&L` mixes a new balance
    #    with profit earned under an old one (see `get_capital_set_at`).
    # 2. `check_max_drawdown`'s peak-equity watermark, which tracks
    #    capital + P&L — so an editable `capital` is itself an input to a
    #    check meant to measure trading LOSSES only. Leaving a stale peak
    #    behind lets a config edit read as a drawdown and halt the engine
    #    with zero real money lost.
    #
    # The peak is CLEARED rather than shifted by the delta or written as
    # the new capital. The delta rule was correct only while equity carried
    # lifetime P&L. Writing `capital` was the first replacement and was
    # also wrong: it assumed equity equals capital right after the re-base,
    # which holds only with no position open — see `clear_peak_equity_paise`
    # for the halt that assumption causes. Clearing defers to
    # `check_max_drawdown`, which seeds the watermark from real equity
    # (including unrealized) on the very next cycle.
    #
    # Both are conditional on the value actually CHANGING: re-saving an
    # unchanged capital (the common case — editing the daily-loss limit)
    # must not discard the running P&L or the real drawdown history.
    #
    # The FIRST save is deliberately NOT a change: with no stored value
    # there is no previous account to separate the new one from, so there
    # is nothing to re-base and an anchor would only discard history for
    # no reason. Such an account keeps the lifetime behaviour, exactly as
    # before this was added.
    #
    # Read the row DIRECTLY rather than through `_read_int`. That helper
    # returns its default on a parse failure, and the default here is the
    # value being written — so an unparseable stored capital made
    # `old == new` unconditionally, and NO capital change would ever stamp
    # an anchor or clear the watermark. Silently, on every save. An
    # unreadable stored value is treated as a change, which is the safe
    # direction: re-basing an account that did not move costs nothing,
    # while missing a re-base is the Rs 61,837 bug.
    stored = session.get(EngineState, _CAPITAL_PAISE_KEY)
    try:
        old_capital = int(stored.value) if stored is not None else int(guardrails.capital)
    except ValueError:
        old_capital = None
    if old_capital != int(guardrails.capital):
        upsert_engine_state(session, _CAPITAL_SET_AT_KEY, dt.datetime.now(dt.UTC).isoformat())
        clear_peak_equity_paise(session)

    upsert_engine_state(session, _CAPITAL_PAISE_KEY, str(int(guardrails.capital)))
    upsert_engine_state(session, _MAX_DAILY_LOSS_PAISE_KEY, str(int(guardrails.max_daily_loss)))
    upsert_engine_state(session, _MAX_POSITION_SIZE_PCT_KEY, str(guardrails.max_position_size_pct))
    upsert_engine_state(session, _MAX_DRAWDOWN_PCT_KEY, str(guardrails.max_drawdown_pct))
    upsert_engine_state(session, _MAX_TRADES_PER_DAY_KEY, str(guardrails.max_trades_per_day))
    upsert_engine_state(session, _MAX_CONCURRENT_POSITIONS_KEY, str(guardrails.max_concurrent_positions))
    upsert_engine_state(session, _RISK_PER_TRADE_PCT_KEY, str(guardrails.risk_per_trade_pct))


_INSTRUMENT_SELECTIONS_KEY = "instrument_selections_json"


@dataclass(frozen=True)
class InstrumentSelection:
    """One instrument's exchange + lot size + whether it's active — the
    live-editable equivalent of `Settings.paper_cycle_instruments`/
    `paper_cycle_exchange`/`paper_cycle_lot_size`, extended to support more
    than one instrument at once (see the plan's Tier 1 "multi-instrument").

    `lot_size` as stored here (via `set_instrument_selections`) is NOT what
    actually gets traded: `get_instrument_selections` resolves the real
    value from the synced `instruments` table (`te.persistence.repos.
    instruments.latest_lot_size`, keyed by the underlying's FRONT-MONTH
    FUTURES contract — see `te.engine.scheduler._fno_lot_size_contracts`)
    on every read and overrides whatever is stored here, falling back to
    the stored value only when nothing has been synced yet for this
    underlying. This is what makes it safe to enable an instrument from the
    dashboard even if the saved `lot_size` is stale or was hand-typed
    wrong."""

    symbol: str
    exchange: str
    lot_size: int
    active: bool = True


def get_instrument_selections(
    session: Session, *, defaults: tuple[InstrumentSelection, ...]
) -> tuple[InstrumentSelection, ...]:
    """Falls back to `defaults` (normally derived from `Settings.
    paper_cycle_instruments`/`paper_cycle_exchange`/`paper_cycle_lot_size` —
    see `te.engine.scheduler._default_cycle_config`) when nothing has been
    saved yet, or the saved value fails to parse — same "never crash, never
    silently trade zero instruments due to a corrupt row" discipline as
    `get_guardrails`.

    Every selection's `lot_size` is then resolved AGAINST the real synced
    `instruments` table (`te.persistence.repos.instruments.latest_lot_size`)
    and overridden when a synced value exists — never trusting the stored
    (possibly stale, or hand-typed at save time) `lot_size` on its own. This
    is what makes it safe to enable an instrument from the dashboard: even
    if a user (or an old saved row) has the wrong lot size on file, what
    actually gets traded uses the broker-verified current value, resolved
    fresh on every read rather than baked in at save time."""
    row = session.get(EngineState, _INSTRUMENT_SELECTIONS_KEY)
    if row is None:
        selections = defaults
    else:
        try:
            raw = json.loads(row.value)
            selections = tuple(
                InstrumentSelection(
                    symbol=str(item["symbol"]),
                    exchange=str(item["exchange"]),
                    lot_size=int(item["lot_size"]),
                    active=bool(item.get("active", True)),
                )
                for item in raw
            )
        except (ValueError, KeyError, TypeError):
            selections = defaults

    return tuple(_with_real_lot_size(session, s) for s in selections)


def _with_real_lot_size(session: Session, selection: InstrumentSelection) -> InstrumentSelection:
    real = latest_lot_size(session, underlying=selection.symbol)
    if real is None:
        if not selection.active:
            return selection
        # Found by review: an ACTIVE instrument whose underlying has never
        # been successfully synced (a brand-new deployment, or an
        # underlying that persistently 404s while `_run_instrument_sync`
        # isolates the failure to just that one symbol) used to silently
        # fall through to the stored/default `lot_size` — typically
        # whatever `Settings.paper_cycle_lot_size` was seeded with for a
        # DIFFERENT underlying (e.g. NIFTY's 65 applied to SENSEX, whose
        # real lot size is 20) — corrupting real sizing/P&L indefinitely
        # with no signal anywhere. Force inactive instead: never trade an
        # underlying on an unconfirmed lot size, matching this project's
        # standing rule that sizing data comes only from the real synced
        # table, never a guess.
        return replace(selection, active=False)
    return replace(selection, lot_size=real)


def set_instrument_selections(session: Session, selections: tuple[InstrumentSelection, ...]) -> None:
    """Validates every entry before writing ANY of them (all-or-nothing),
    same convention as `set_guardrails`. Does not commit."""
    seen: set[tuple[str, str]] = set()
    for sel in selections:
        if not sel.symbol:
            raise ValueError("instrument symbol must not be empty")
        if not sel.exchange:
            raise ValueError(f"instrument {sel.symbol!r} must have a non-empty exchange")
        if sel.lot_size <= 0:
            raise ValueError(f"instrument {sel.symbol!r} lot_size must be positive, got {sel.lot_size}")
        key = (sel.symbol, sel.exchange)
        if key in seen:
            raise ValueError(f"duplicate instrument selection for {sel.symbol!r} on {sel.exchange!r}")
        seen.add(key)

    payload = json.dumps(
        [
            {"symbol": s.symbol, "exchange": s.exchange, "lot_size": s.lot_size, "active": s.active}
            for s in selections
        ]
    )
    upsert_engine_state(session, _INSTRUMENT_SELECTIONS_KEY, payload)


def guardrails_defaults_from_settings(settings: Settings) -> AccountGuardrails:
    """The single place `Settings.paper_cycle_*` env vars get turned into an
    `AccountGuardrails` default — shared by `te.engine.scheduler`'s
    `PaperCycleRunner` (the fallback when no `engine_state` row exists yet)
    and the `/api/engine/guardrails` router (so `GET` shows the SAME values
    the paper cycle would actually fall back to, not a second, possibly
    drifted copy of the same literals)."""
    return AccountGuardrails(
        capital=Paise(settings.paper_cycle_capital_paise),
        max_daily_loss=Paise(settings.paper_cycle_max_daily_loss_paise),
        # These two have no env-var equivalent, so the default IS the
        # policy. `Decimal(100)` meant "disabled" for both — a drawdown
        # breaker that never trips and a position cap that permits the whole
        # account in one contract. The ceilings are the sane default here,
        # not merely the upper bound.
        max_position_size_pct=MAX_POSITION_SIZE_PCT_CEILING,
        max_drawdown_pct=MAX_DRAWDOWN_PCT_CEILING,
        max_trades_per_day=settings.paper_cycle_max_trades_per_day,
        max_concurrent_positions=settings.paper_cycle_max_concurrent_positions,
        risk_per_trade_pct=settings.paper_cycle_risk_budget_pct,
    )


_LAST_CYCLE_PIPELINE_KEY = "last_cycle_pipeline_json"

#: Fixed order/keys the dashboard's `PipelineStrip` renders — mirrors
#: `dashboard/src/types/dashboard-snapshot.ts`'s `PipelineStageKey`.
PIPELINE_STAGE_KEYS: tuple[str, ...] = ("fetch", "analyze", "risk", "decide", "act")


@dataclass(frozen=True)
class PipelineStageTiming:
    """One stage's real, measured wall-clock cost from the most recently
    completed `run_entry_cycle` call. `reached=False` means this stage
    genuinely did not run this cycle (e.g. every instrument was skipped
    before sizing, so `decide`/`act` never fired) — never fabricated as
    `done` just to fill the bar."""

    reached: bool
    elapsed_ms: int


@dataclass(frozen=True)
class LastCyclePipeline:
    cycle_id: int
    as_of: dt.datetime
    stages: dict[str, PipelineStageTiming]


def set_last_cycle_pipeline(
    session: Session, *, cycle_id: int, as_of: dt.datetime, stages: dict[str, PipelineStageTiming]
) -> None:
    """Called once at the end of `run_entry_cycle`, in its own session —
    same one-writer-per-key convention as every other setter here. Does not
    commit; caller owns the transaction boundary."""
    payload = json.dumps(
        {
            "cycle_id": cycle_id,
            "as_of": to_utc(as_of, name="as_of").isoformat(),
            "stages": {k: {"reached": v.reached, "elapsed_ms": v.elapsed_ms} for k, v in stages.items()},
        }
    )
    upsert_engine_state(session, _LAST_CYCLE_PIPELINE_KEY, payload)


def get_last_cycle_pipeline(session: Session) -> LastCyclePipeline | None:
    """`None` when no cycle has completed yet, or the stored row fails to
    parse — same lenient-read convention as the rest of this module."""
    row = session.get(EngineState, _LAST_CYCLE_PIPELINE_KEY)
    if row is None:
        return None
    try:
        raw = json.loads(row.value)
        stages = {
            k: PipelineStageTiming(reached=bool(v["reached"]), elapsed_ms=int(v["elapsed_ms"]))
            for k, v in raw["stages"].items()
        }
        return LastCyclePipeline(
            cycle_id=int(raw["cycle_id"]), as_of=dt.datetime.fromisoformat(raw["as_of"]), stages=stages
        )
    except (ValueError, KeyError, TypeError):
        return None


def instrument_selections_defaults_from_settings(settings: Settings) -> tuple[InstrumentSelection, ...]:
    """Seeds all 4 known F&O underlyings (`te.domain.symbols.
    FNO_UNDERLYING_EXCHANGES` — NIFTY/BANKNIFTY on NFO, SENSEX/BANKEX on
    BFO), each with its CORRECT real exchange baked in, `active` set only
    for whatever's in `Settings.paper_cycle_instruments` (today's
    single-instrument behaviour, unchanged when nothing has been saved via
    the API yet) and inactive otherwise. `lot_size` is a real fallback (the
    same one env-configured behaviour always used) — `get_instrument_
    selections` overrides it from the synced `instruments` table once one
    exists, so this is never what actually gets traded once a sync has run.

    Found live: before this, a symbol NOT in `Settings.paper_cycle_
    instruments` (e.g. SENSEX/BANKEX, since only NIFTY is configured there
    by default) had no seeded default at all — a user enabling it from the
    dashboard would have to hand-type BOTH exchange and lot size, with no
    guardrail stopping them from picking NFO (wrong exchange for SENSEX/
    BANKEX, 404s against the real broker). Now every known underlying is
    pre-seeded with the one correct exchange, and toggling `active` is the
    only thing a dashboard save can actually change."""
    active_symbols = set(settings.paper_cycle_instruments)
    return tuple(
        InstrumentSelection(
            symbol=symbol,
            exchange=exchange,
            lot_size=settings.paper_cycle_lot_size,
            active=symbol in active_symbols,
        )
        for symbol, exchange in FNO_UNDERLYING_EXCHANGES
    )
