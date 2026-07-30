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


@dataclass(frozen=True)
class AccountGuardrails:
    capital: Paise
    max_daily_loss: Paise
    max_position_size_pct: Decimal
    max_drawdown_pct: Decimal
    max_trades_per_day: int
    max_concurrent_positions: int
    risk_per_trade_pct: Decimal


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


def get_guardrails(session: Session, *, defaults: AccountGuardrails) -> AccountGuardrails:
    """Reads each of the 7 guardrail keys independently, falling back to the
    corresponding field on `defaults` for any key that is missing or fails
    to parse — never a single all-or-nothing fallback, so a partially-saved
    guardrails row (or a single corrupted key) can't take the rest down with
    it. `defaults` is normally `Settings.paper_cycle_*`-derived (see
    `te.engine.scheduler._default_cycle_config`), so an operator who never
    touches the Settings page gets exactly today's env-var behaviour,
    unchanged."""
    return AccountGuardrails(
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

    # `te.risk.limits.check_max_drawdown`'s peak-equity watermark tracks
    # capital + P&L — so an editable `capital` field is itself an input to
    # a check that's supposed to measure trading LOSSES only. Found by
    # review: lowering capital here without rebasing the watermark makes
    # `check_max_drawdown` see the edit as a loss and can halt the engine
    # on a config change with zero real money lost. Shifting the stored
    # peak by the same delta keeps drawdown-from-trading-performance
    # unaffected by a capital edit either direction.
    old_capital = _read_int(session, _CAPITAL_PAISE_KEY, int(guardrails.capital))
    capital_delta = int(guardrails.capital) - old_capital
    if capital_delta != 0:
        stored_peak = get_peak_equity_paise(session)
        if stored_peak is not None:
            set_peak_equity_paise(session, Paise(int(stored_peak) + capital_delta))

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
        max_position_size_pct=Decimal(100),  # no env-var equivalent existed before tonight
        max_drawdown_pct=Decimal(100),  # ditto — was never enforced pre-Tier-1, see the plan's note
        max_trades_per_day=settings.paper_cycle_max_trades_per_day,
        max_concurrent_positions=settings.paper_cycle_max_concurrent_positions,
        risk_per_trade_pct=settings.paper_cycle_risk_budget_pct,
    )


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
