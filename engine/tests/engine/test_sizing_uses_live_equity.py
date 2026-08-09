"""Position size must follow the account, not a number typed into a form.

Until 2026-08-05 `run_entry_cycle` passed `config.capital` — the static,
dashboard-configured figure — into `size_position`, while computing true
account equity a few hundred lines earlier and handing THAT to the drawdown
breaker. So the engine knew what the account was worth and sized off
something else.

Both directions were wrong. Profits never raised buying power, so the
account could not compound. And after losses the engine kept staking the
original capital, which means the real risk per trade GROWS precisely while
the account is shrinking — 3% of Rs 30,000 staked against a Rs 25,000
balance is 3.6% of what is actually there. That is how a drawdown turns into
a wipe-out.

It also silently invalidated every backtest in this repo against live:
`strategy_lab.run_many` and `sweep.replay` both take `compound_equity` and DO
re-size off running equity, so measured results assumed a discipline
production did not have.

These tests assert the direction of the effect on REAL closed trades in the
database, never a mocked balance — the bug was precisely that a real balance
existed and went unread.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from pathlib import Path

import pandas as pd
import pytest

from te.broker.simulated import SimulatedBroker
from te.data.barstore import BAR_COLUMNS, BarStore
from te.data.charges_loader import load_charge_rate_table
from te.domain.clock import IST
from te.domain.costs import CostModel, select_rates
from te.domain.geometry import AbsolutePointGeometry
from te.domain.money import Paise
from te.engine.cycle import CycleConfig, run_entry_cycle, run_exit_cycle
from te.execution.manager import ExecutionManager
from te.execution.store import OrderEventStore
from te.persistence.db import make_engine, make_session_factory
from te.persistence.models import Base, OpenPositionRow, TradeRow
from te.risk.limits import RiskLimitsConfig

_CHARGES = Path(__file__).resolve().parents[2] / "config" / "charges.yaml"
INSTRUMENT = "NIFTY30JUN2626500CE"
EXCHANGE = "NFO"
ON = dt.date(2026, 7, 29)


class _NoLimiter:
    def acquire(self, n: int = 1) -> None:
        pass


def _open(minute: int) -> dt.datetime:
    return dt.datetime(2026, 7, 29, 9, 15, tzinfo=IST) + dt.timedelta(minutes=minute)


def _bar(ts: dt.datetime, *, o: float, h: float, low: float, c: float, v: int) -> dict[str, object]:
    return {
        "symbol": INSTRUMENT, "exchange": EXCHANGE, "event_ts": ts, "interval": "1m",
        "o": o, "h": h, "l": low, "c": c, "v": v, "oi": 0, "ingested_at": ts, "source": "test",
    }


@pytest.fixture
def cost_model() -> CostModel:
    return CostModel(select_rates(load_charge_rate_table(_CHARGES), ON))


@pytest.fixture
def session_factory(tmp_path: Path):  # noqa: ANN201
    engine = make_engine(f"sqlite:///{tmp_path / 'equity_sizing.db'}")
    Base.metadata.create_all(engine)
    return make_session_factory(engine)


@pytest.fixture
def store(tmp_path: Path) -> BarStore:
    """A REAL 60-minute opening range then a clean upward breakout.

    The range length is `OrbParams.opening_range_minutes` (60), so bars must
    span 09:15-10:14 before anything after 10:15 can be a breakout at all —
    and the breakout bar has to be the FIRST close beyond the range, since
    the rule is edge-triggered.
    """
    s = BarStore(tmp_path / "bars")
    rows = [_bar(_open(i), o=30, h=31, low=29, c=30, v=1_000) for i in range(60)]
    rows.append(_bar(_open(61), o=30, h=38, low=30, c=36, v=5_000))
    s.append(pd.DataFrame(rows, columns=list(BAR_COLUMNS)))
    return s


def _config(capital: Paise) -> CycleConfig:
    return CycleConfig(
        mode="paper",
        strategy_name="orb",
        instruments=(INSTRUMENT,),
        exchange=EXCHANGE,
        lot_size=65,
        capital=capital,
        risk_budget_pct=Decimal(10),
        min_edge_multiple=Decimal("1.2"),
        exit_geometry=AbsolutePointGeometry(
            stop_distance=Paise(700), target_distance=Paise(1_500), trailing_distance=Paise(300)
        ),
        max_hold=dt.timedelta(hours=3),
        hard_exit_by=dt.time(15, 20),
        risk_limits=RiskLimitsConfig(
            max_daily_loss_paise=Paise(100_000_00), max_concurrent_positions=5, max_trades_per_day=20
        ),
    )


def _settle(session_factory, net_paise: int) -> None:  # noqa: ANN001
    """One CLOSED trade carrying `net_paise`. This is the only input — the
    cycle must discover the balance change by reading its own trade history,
    exactly as it does live."""
    with session_factory() as session:
        session.add(
            TradeRow(
                client_order_id=f"settled-{net_paise}",
                symbol="NIFTY30JUN2626000CE", exchange=EXCHANGE, strategy="orb", direction="long_call",
                lots=1, lot_size=65, entry_premium_paise=1_000,
                exit_premium_paise=1_000 + (net_paise // 65), gross_pnl_paise=net_paise,
                costs_paise=0, net_pnl_paise=net_paise, exit_reason="target", mode="paper",
                opened_at=dt.datetime(2026, 7, 28, 4, 0, tzinfo=dt.UTC),
                closed_at=dt.datetime(2026, 7, 28, 5, 0, tzinfo=dt.UTC),
                stop_paise=800, target_paise=1_200,
            )
        )
        session.commit()


def _lots_taken(  # noqa: ANN001
    session_factory, store: BarStore, cost_model: CostModel, capital: Paise, *, minute: int = 62
) -> int:
    """Lots on the position this cycle opened, or 0 if it opened none.

    `minute` must DIFFER between two calls against the same database:
    `cycle_evaluations.evaluation_id` is unique and derived from the cycle
    timestamp, so replaying the identical `as_of` is an integrity error, not
    a second decision. Any minute past the 10:15 range end sees the same
    single breakout bar, so this changes the clock without changing the
    signal."""
    broker = SimulatedBroker(cost_model=cost_model, on=ON)
    execution = ExecutionManager(session_factory, OrderEventStore(session_factory), broker, _NoLimiter())
    run_entry_cycle(
        session_factory=session_factory, store=store, execution=execution, cost_model=cost_model,
        config=_config(capital), as_of=_open(minute),
    )
    with session_factory() as session:
        row = session.query(OpenPositionRow).order_by(OpenPositionRow.id.desc()).first()
        return row.lots if row is not None else 0


def test_a_realised_profit_increases_buying_power(session_factory, store, cost_model) -> None:  # noqa: ANN001
    """The behaviour the owner expected and the engine did not have: make
    money, and the next trade may be larger.

    Rs 10,000 capital at 10% risk budget = Rs 1,000; risk per lot is
    `stop_distance 700p x lot 65` = Rs 455, so `100_000 // 45_500 = 2` lots.
    A profit small enough to leave the budget under Rs 1,820 (4 lots'
    worth) sizes IDENTICALLY whether or not it ever reaches sizing at all —
    `2 >= 2` is true by construction and would pass even if profits were
    silently discarded. +Rs 9,000 crosses a real lot boundary: equity
    Rs 19,000 gives a Rs 1,900 budget, `190_000 // 45_500 = 4` lots — a
    STRICT increase that can only happen if the profit reached sizing."""
    flat = _lots_taken(session_factory, store, cost_model, Paise(1_000_000))
    assert flat == 2, "fixture assumption changed — recompute the boundary-crossing profit"

    fresh = session_factory
    _settle(fresh, 9_000_00)  # +Rs 9,000 realised
    with fresh() as session:
        session.query(OpenPositionRow).delete()
        session.commit()
    after_profit = _lots_taken(fresh, store, cost_model, Paise(1_000_000), minute=63)

    assert after_profit > flat, "a real profit must strictly increase buying power"
    assert after_profit == 4


def test_a_realised_loss_reduces_buying_power(session_factory, store, cost_model) -> None:  # noqa: ANN001
    """The direction that actually protects the account. Sizing off static
    capital after a loss quietly RAISES the real risk percentage at the worst
    possible moment."""
    baseline = _lots_taken(session_factory, store, cost_model, Paise(1_000_000))
    assert baseline > 1, "fixture must size to several lots or a reduction cannot be observed"

    with session_factory() as session:
        session.query(OpenPositionRow).delete()
        session.commit()
    # -Rs 3,000 on Rs 10,000, and the size has to be chosen to actually
    # BITE: at 10% risk the budget is Rs 1,000 against Rs 455 of risk per
    # lot, so 2 lots fit. Only a loss taking equity below Rs 9,100 drops
    # that to 1. A smaller loss passes identically either way and would
    # prove nothing — the original -Rs 700 did exactly that.
    _settle(session_factory, -3_000_00)  # -Rs 3,000 realised

    after_loss = _lots_taken(session_factory, store, cost_model, Paise(1_000_000), minute=63)

    assert after_loss < baseline, "a smaller account still staked the original capital"


def test_a_wiped_out_account_takes_no_trade(session_factory, store, cost_model) -> None:  # noqa: ANN001
    """`max(0, ...)` in the cycle. Losses exceeding capital must reject with
    a real reason, never wrap into a negative budget that sizes as huge."""
    _settle(session_factory, -2_000_00)  # -Rs 2,000 against Rs 1,000 capital

    assert _lots_taken(session_factory, store, cost_model, Paise(100_000)) == 0


_SECOND_INSTRUMENT = "BANKNIFTY30JUL2652000CE"


def _second_instrument_store(tmp_path: Path) -> BarStore:
    """The same clean 60-minute-range-then-breakout shape as `store`, for a
    SECOND, independent underlying — needed so the second entry cycle below
    does not collide with the first instrument's per-session entry cap."""
    s = BarStore(tmp_path / "bars2")
    rows = [_bar(_open(i), o=30, h=31, low=29, c=30, v=1_000) for i in range(60)]
    rows.append(_bar(_open(61), o=30, h=38, low=30, c=36, v=5_000))
    for row in rows:
        row = dict(row)
        row["symbol"] = _SECOND_INSTRUMENT
        s.append(pd.DataFrame([row], columns=list(BAR_COLUMNS)))
    return s


def test_open_position_marks_do_not_resize_the_next_entry(
    session_factory, store, cost_model, tmp_path: Path
) -> None:  # noqa: ANN001
    """Sizing equity is REALISED-only, while the drawdown breaker's equity
    also carries unrealised. An open position's minute-by-minute paper
    profit must not change what the next entry stakes — it has not settled
    and can reverse before it does.

    The position from the first call is left OPEN and carrying a real
    unrealised mark (previously this test DELETED that row before the
    second cycle ran, which made the deliberate realised/unrealised split
    this test exists to protect impossible to observe — with no open
    position, `unrealized_pnl_paise` returns exactly 0 no matter what the
    code does with it). The second entry is on a DIFFERENT underlying so
    the per-session entry cap on the FIRST symbol cannot mask the result.

    Rs 30,000 capital, so the first position sizes to several lots and the
    unrealised profit below (~Rs 5,265 on 6 lots) is big enough to cross a
    real lot boundary on the SECOND entry's own sizing (budget Rs 3,000 ->
    Rs 3,526, 6 lots -> 7) if it ever leaked in — a smaller profit would size
    identically either way and prove nothing, the same gap finding 6 closes."""
    capital = Paise(3_000_000)
    first = _lots_taken(session_factory, store, cost_model, capital)
    assert first > 0
    with session_factory() as session:
        assert session.query(OpenPositionRow).count() == 1
        open_row = session.query(OpenPositionRow).one()
        entry_premium = open_row.entry_premium_paise
        target = open_row.target_paise

    # A REAL, large paper profit on the still-open NIFTY position — short of
    # target so it stays open, comfortably above the trailing activation so
    # the ratchet doesn't accidentally close it either.
    profit_mark = Paise(entry_premium + (target - entry_premium) * 9 // 10)
    closed = run_exit_cycle(
        session_factory=session_factory,
        execution=ExecutionManager(
            session_factory, OrderEventStore(session_factory), SimulatedBroker(cost_model=cost_model, on=ON), _NoLimiter()
        ),
        cost_model=cost_model,
        current_premium=lambda row: profit_mark,
        as_of=_open(62),
    )
    assert closed == [], "the position must still be open, carrying a real unrealised profit"
    with session_factory() as session:
        assert session.query(OpenPositionRow).filter(OpenPositionRow.closed_at.is_(None)).count() == 1

    second_store = _second_instrument_store(tmp_path)
    second_config = CycleConfig(
        mode="paper",
        strategy_name="orb",
        instruments=(_SECOND_INSTRUMENT,),
        exchange=EXCHANGE,
        lot_size=65,
        capital=capital,
        risk_budget_pct=Decimal(10),
        min_edge_multiple=Decimal("1.2"),
        exit_geometry=AbsolutePointGeometry(
            stop_distance=Paise(700), target_distance=Paise(1_500), trailing_distance=Paise(300)
        ),
        max_hold=dt.timedelta(hours=3),
        hard_exit_by=dt.time(15, 20),
        risk_limits=RiskLimitsConfig(
            max_daily_loss_paise=Paise(100_000_00), max_concurrent_positions=5, max_trades_per_day=20
        ),
    )
    broker = SimulatedBroker(cost_model=cost_model, on=ON)
    execution = ExecutionManager(session_factory, OrderEventStore(session_factory), broker, _NoLimiter())
    run_entry_cycle(
        session_factory=session_factory,
        store=second_store,
        execution=execution,
        cost_model=cost_model,
        config=second_config,
        as_of=_open(63),
        # The open NIFTY position's mark, fed through explicitly so the
        # entry cycle's own `unrealized_pnl_paise` call actually sees the
        # real paper profit rather than falling back to a bar-derived price
        # near entry (which would barely exercise the guard).
        current_premium=lambda row: profit_mark if row.symbol == INSTRUMENT else None,
    )

    with session_factory() as session:
        second_row = (
            session.query(OpenPositionRow)
            .filter(OpenPositionRow.symbol == _SECOND_INSTRUMENT)
            .order_by(OpenPositionRow.id.desc())
            .first()
        )
    assert second_row is not None, "the second underlying's entry must not have been blocked"
    assert second_row.lots == first, "an unsettled open-position mark must not resize the next entry"
