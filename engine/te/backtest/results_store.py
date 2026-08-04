"""Persists strategy backtest results so the dashboard can show them.

Stored as one JSON blob in `engine_state`, the same pattern the trading
calendar uses — `EngineState` is already a generic key/value table, so this
needs no migration and no new model.

### What is deliberately NOT stored

Individual trades. The Strategies page needs a summary per strategy, and
keeping ~40,000 trade rows to render 32 cards would be a large table with one
consumer. A run that needs the trades re-runs the backtest, which is
reproducible.

### The honesty constraint this file exists to enforce

`deflated` is the only score that may reach a page, and it is only
meaningful alongside the trial count it was computed against. The two are
therefore stored together and surfaced together — a deflated score without
its N is exactly as misleading as a raw one, because the reader cannot tell
whether it was discounted for 2 trials or 200.

The same rule now applies to money. `net_pnl_paise` travels with the capital
it was earned on, with the count of signals that were unaffordable, and with
whether a breaker ended the run early — because a rupee P&L shorn of those is
the most misleading number this file could publish. Measured 2026-08-04: ORB
on Rs 30,000 reports -Rs 6,058 over what looks like 636 sessions, but the
drawdown breaker had stopped it after 17, and 117 of its signals were never
affordable. The P&L alone tells none of that.
"""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import asdict, dataclass

from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from te.backtest.strategy_lab import BacktestResult
from te.engine.state import upsert_engine_state
from te.persistence.models import EngineState

_RESULTS_KEY = "strategy_backtests_json"


@dataclass(frozen=True)
class StoredResult:
    """One strategy's backtest summary, as shown on the Strategies page."""

    strategy: str
    instrument: str
    trades: int
    win_rate: float
    mean_r: float
    sharpe: float
    t_stat: float
    deflated: float
    n_trials_at_scoring: int
    first_day: str | None
    last_day: str | None
    #: What the backtest actually traded, so a reader can tell whether the
    #: number describes the settings the engine is running.
    stop_pct: float
    target_pct: float
    max_hold_minutes: int
    strikes_out_of_the_money: int
    run_id: str
    measured_at: str
    # --- Money. Everything above this line is unit-free ------------------
    #
    # Added 2026-08-04. Until then this type was structurally incapable of
    # carrying a rupee figure, so the Strategies page could only ever show
    # `mean_r`/`deflated` — research scores that cannot answer "how much
    # would this have made". Every field below defaults, so results stored
    # under the older shape still load rather than being dropped by
    # `load_results`' shape guard.
    #
    #: Total realized net P&L over the run.
    net_pnl_paise: int = 0
    #: What it was sized against. Stored WITH the P&L because a rupee figure
    #: alone is unreadable: -Rs 6,000 is a fifth of a Rs 30,000 account and a
    #: rounding error on a Rs 30,00,000 one.
    capital_paise: int = 0
    #: `capital_paise + net_pnl_paise` at the point the run ended.
    final_equity_paise: int = 0
    # --- What the strategy was NOT allowed to do -------------------------
    #
    # Per `honest-metrics`: a rupee P&L that hides how many signals were
    # rejected, or that the run was halted early, overstates what the
    # strategy did. These travel WITH the money figure, never separately.
    #
    #: Labelled signals `size_position` could not afford at `capital_paise`.
    unaffordable: int = 0
    #: Days cut short by the daily loss limit.
    halted_days: int = 0
    #: Days cut short by the consecutive-loss standdown.
    standdown_days: int = 0
    #: Whether the max-drawdown breaker (or an account wipe-out) ended the
    #: run before `last_day`. When True, the P&L above describes a SHORTER
    #: period than the date range suggests — the single most misleading way
    #: to read this row, hence a first-class field rather than a footnote.
    drawdown_halted: bool = False

    @property
    def beats_luck(self) -> bool:
        return self.deflated > 0.95


def save_results(
    session: Session,
    results: list[BacktestResult],
    *,
    run_id: str,
    stop_pct: float,
    target_pct: float,
    max_hold_minutes: int,
    strikes_out_of_the_money: int,
    measured_at: dt.datetime | None = None,
) -> list[StoredResult]:
    """Replaces the stored results with `results`.

    A REPLACE rather than a merge: a run backtests the whole library at one
    set of settings, and mixing a fresh 20%-stop result beside a stale
    30%-stop one under the same heading would make the page silently
    incomparable row to row.
    """
    stamp = (measured_at or dt.datetime.now(dt.UTC)).isoformat()
    stored = [
        StoredResult(
            strategy=r.strategy,
            instrument=r.instrument,
            trades=r.trades,
            win_rate=r.win_rate,
            mean_r=r.mean_r,
            sharpe=r.sharpe,
            t_stat=r.t_stat,
            deflated=r.deflated,
            n_trials_at_scoring=r.n_trials_at_scoring,
            first_day=r.first_day.isoformat() if r.first_day else None,
            last_day=r.last_day.isoformat() if r.last_day else None,
            stop_pct=stop_pct,
            target_pct=target_pct,
            max_hold_minutes=max_hold_minutes,
            strikes_out_of_the_money=strikes_out_of_the_money,
            run_id=run_id,
            measured_at=stamp,
            net_pnl_paise=r.net_pnl_paise,
            capital_paise=r.capital_paise,
            final_equity_paise=r.final_equity_paise,
            unaffordable=r.unaffordable,
            halted_days=r.halted_days,
            standdown_days=r.standdown_days,
            drawdown_halted=r.drawdown_halted,
        )
        for r in results
    ]
    upsert_engine_state(session, _RESULTS_KEY, json.dumps([asdict(s) for s in stored]))
    return stored


def load_results(session: Session) -> dict[str, StoredResult]:
    """Stored results keyed by strategy name, or empty when none exist.

    Returns empty rather than raising on a missing table so the API can
    serve honest zero-state on a database that has never had a backtest run
    against it — the same failure posture `get_calendar` takes.
    """
    try:
        row = session.get(EngineState, _RESULTS_KEY)
    except OperationalError:
        return {}
    if row is None or not row.value:
        return {}
    try:
        payload = json.loads(row.value)
    except json.JSONDecodeError:
        return {}
    results: dict[str, StoredResult] = {}
    for item in payload:
        try:
            results[item["strategy"]] = StoredResult(**item)
        except (TypeError, KeyError):
            # A row written by an older shape is skipped rather than
            # crashing the page. Dropping one card is recoverable; a 500 on
            # the Strategies route is not.
            continue
    return results
