"""What a stored backtest result is allowed to say about MONEY.

`StoredResult` carried only unit-free scores (`mean_r`, `sharpe`, `deflated`)
until 2026-08-04, so the Strategies page could not answer the one question the
owner actually asks: how much would this have made. Adding rupees to a page
that previously showed only research scores creates two new ways to mislead,
and both are pinned here.

The first is a shape break. `load_results` deliberately SKIPS any row it
cannot construct rather than crashing the page, so a new required field would
silently empty the Strategies page instead of failing loudly — the worst of
both worlds. Every money field therefore defaults.

The second is money without its context. -Rs 6,058 reads as a small loss until
you know it was a fifth of the account, that the drawdown breaker had already
stopped the run 17 sessions into a 636-session window, and that 117 signals
were never affordable in the first place. Those numbers travel together or the
P&L is a lie by omission — see `te.backtest.results_store`'s module docstring
for the real measurement this rule comes from.
"""

from __future__ import annotations

import datetime as dt
import json

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import sessionmaker

from te.backtest.results_store import _RESULTS_KEY, StoredResult, load_results, save_results
from te.backtest.strategy_lab import BacktestResult
from te.engine.state import upsert_engine_state
from te.persistence.models import Base


@pytest.fixture
def session(tmp_path):  # noqa: ANN001, ANN201
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as s:
        yield s


def _result(**overrides) -> BacktestResult:  # noqa: ANN003
    base = {
        "strategy": "orb",
        "instrument": "NIFTY",
        "trades": 37,
        "unlabelled": 1,
        "win_rate": 0.29,
        "mean_r": -0.36,
        "sharpe": -0.4,
        "t_stat": -2.1,
        "deflated": 0.0,
        "n_trials_at_scoring": 147,
        "first_day": dt.date(2024, 1, 1),
        "last_day": dt.date(2026, 8, 4),
        # The real 2026-08-04 measurement on Rs 30,000 capital.
        "net_pnl_paise": -605_775,
        "capital_paise": 3_000_000,
        "final_equity_paise": 2_394_225,
        "unaffordable": 117,
        "halted_days": 3,
        "standdown_days": 2,
        "drawdown_halted": True,
    }
    return BacktestResult(**{**base, **overrides})


def _save(session, result: BacktestResult) -> StoredResult:  # noqa: ANN001
    stored = save_results(
        session,
        [result],
        run_id="test",
        stop_pct=20.0,
        target_pct=20.0,
        max_hold_minutes=180,
        strikes_out_of_the_money=0,
    )
    return stored[0]


def test_the_money_survives_a_save_and_load_round_trip(session) -> None:  # noqa: ANN001
    """The whole point of the field existing."""
    _save(session, _result())

    loaded = load_results(session)["orb"]

    assert loaded.net_pnl_paise == -605_775
    assert loaded.capital_paise == 3_000_000
    assert loaded.final_equity_paise == 2_394_225


def test_the_pnl_never_travels_without_the_capital_it_was_earned_on(session) -> None:  # noqa: ANN001
    """-Rs 6,058 is a fifth of a Rs 30,000 account and a rounding error on a
    Rs 30,00,000 one. A reader cannot tell which from the P&L alone, so the
    two are stored as one fact."""
    stored = _save(session, _result())

    assert stored.capital_paise > 0, "a rupee P&L with no capital beside it is unreadable"
    # The reader can therefore always derive the only figure that compares
    # across account sizes.
    assert stored.net_pnl_paise / stored.capital_paise == pytest.approx(-0.20192, abs=1e-5)


def test_a_run_the_breaker_stopped_early_says_so(session) -> None:  # noqa: ANN001
    """The single most misleading way to read a row: the date range spans
    636 sessions, but the drawdown breaker ended this run after 17. Without
    this flag the P&L looks like it describes two and a half years."""
    _save(session, _result())
    loaded = load_results(session)["orb"]

    assert loaded.drawdown_halted is True
    assert loaded.halted_days == 3
    assert loaded.standdown_days == 2


def test_rejected_signals_are_visible_beside_the_trades_that_happened(session) -> None:  # noqa: ANN001
    """117 of this strategy's signals were never affordable at Rs 30,000.
    Reporting only the 37 it could take would overstate how much it traded
    by a factor of four."""
    _save(session, _result())
    loaded = load_results(session)["orb"]

    assert loaded.unaffordable == 117
    assert loaded.trades == 37


def test_a_result_stored_before_money_existed_still_loads(session) -> None:  # noqa: ANN001
    """`load_results` skips rows it cannot construct, so a money field
    without a default would silently blank the Strategies page rather than
    fail loudly. This writes the pre-2026-08-04 shape by hand — exactly the
    JSON already sitting in the live database — and requires it back."""
    legacy = {
        "strategy": "orb",
        "instrument": "NIFTY",
        "trades": 1274,
        "win_rate": 0.471,
        "mean_r": -0.1017,
        "sharpe": -0.1,
        "t_stat": -3.5,
        "deflated": 0.0,
        "n_trials_at_scoring": 147,
        "first_day": "2024-01-01",
        "last_day": "2026-08-04",
        "stop_pct": 20.0,
        "target_pct": 20.0,
        "max_hold_minutes": 180,
        "strikes_out_of_the_money": 0,
        "run_id": "all-strategies-0otm",
        "measured_at": "2026-08-04T10:00:00+00:00",
    }
    upsert_engine_state(session, _RESULTS_KEY, json.dumps([legacy]))

    loaded = load_results(session)

    assert "orb" in loaded, "an older stored shape must not vanish from the page"
    assert loaded["orb"].mean_r == -0.1017
    # Honest zero, not a fabricated figure: this run genuinely never measured money.
    assert loaded["orb"].net_pnl_paise == 0
    assert loaded["orb"].capital_paise == 0
    assert loaded["orb"].drawdown_halted is False


def test_a_run_with_no_sizing_reports_zero_money_rather_than_a_guess(session) -> None:  # noqa: ANN001
    """`scripts/backtest_all_strategies.py` does not pass real capital, so
    its results have no meaningful rupee figure. Per `honest-metrics` that
    must read as an explicit zero, never as a plausible-looking number."""
    stored = _save(session, _result(net_pnl_paise=0, capital_paise=0, final_equity_paise=0, unaffordable=0))

    assert stored.net_pnl_paise == 0
    assert stored.capital_paise == 0
