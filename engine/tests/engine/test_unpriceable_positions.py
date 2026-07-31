"""An option position that cannot be priced must never read as "unchanged".

An option contract is chosen dynamically per signal, so it is never in the
WS recorder's subscription list (only the four index spot symbols are) and
has NO recorded bars. Its only live mark is a per-cycle REST quote.

When that quote failed, the old code fell back to bars — always empty for an
option — and then to the position's own ENTRY premium. That reads as "price
unchanged", with three consequences, all of which these tests pin shut:

1. `evaluate_position` sees `current == entry`, so `entry >= target` and
   `entry <= stop` are both false: no stop, target or trailing exit could
   fire. Only the time exit could ever close the position.
2. `unrealized_pnl_paise` returned roughly `-costs` for every open position
   no matter how far underwater the book was, so the daily-loss halt and the
   drawdown breaker were blind to open losses of any size.
3. Any exit taken in that state recorded a fabricated exit price and a gross
   P&L of exactly zero straight into `trades`.

The fix makes "unknown" an explicit `None` that the caller must handle, and
persists the last SUCCESSFUL mark so a fallback is a real, if stale, price.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest
from sqlalchemy.orm import Session, sessionmaker

from te.broker.simulated import SimulatedBroker
from te.data.charges_loader import load_charge_rate_table
from te.domain.costs import CostModel, select_rates
from te.domain.money import Paise
from te.domain.signal import ExitPlan
from te.engine.cycle import run_exit_cycle, unrealized_pnl_paise
from te.execution.manager import ExecutionManager
from te.execution.store import OrderEventStore
from te.persistence.db import make_engine, make_session_factory
from te.persistence.models import Base, OpenPositionRow, RiskEventRow, TradeRow
from te.persistence.repos.paper_trading import insert_open_position

_CHARGES_PATH = Path(__file__).resolve().parents[2] / "config" / "charges.yaml"
ON = dt.date(2026, 7, 31)
ENTRY = Paise(10_000)  # Rs 100.00
OPENED_AT = dt.datetime(2026, 7, 31, 5, 0, tzinfo=dt.UTC)  # 10:30 IST


class _NoLimiter:
    def acquire(self, n: int = 1) -> None:
        pass


@pytest.fixture
def cost_model() -> CostModel:
    return CostModel(select_rates(load_charge_rate_table(_CHARGES_PATH), ON))


@pytest.fixture
def session_factory(tmp_path: Path):  # noqa: ANN201
    engine = make_engine(f"sqlite:///{tmp_path / 'unpriceable.db'}")
    Base.metadata.create_all(engine)
    return make_session_factory(engine)


@pytest.fixture
def execution(session_factory, cost_model: CostModel):  # noqa: ANN001, ANN201
    broker = SimulatedBroker(cost_model=cost_model, on=ON)
    return ExecutionManager(session_factory, OrderEventStore(session_factory), broker, _NoLimiter())


def _seed(factory: sessionmaker[Session], *, hard_exit_by: dt.time = dt.time(15, 20)) -> None:
    plan = ExitPlan(
        entry_premium=ENTRY,
        stop=Paise(8_000),  # -20%
        trailing_distance=Paise(1_500),
        target=Paise(14_000),  # +40%
        max_hold=dt.timedelta(hours=3),
        hard_exit_by=hard_exit_by,
    )
    with factory() as session:
        insert_open_position(
            session,
            client_order_id="c1",
            symbol="NIFTY04AUG2624400CE",
            exchange="NFO",
            strategy="orb",
            direction="long_call",
            lots=1,
            lot_size=65,
            entry_premium=ENTRY,
            exit_plan=plan,
            opened_at=OPENED_AT,
        )
        session.commit()


def test_an_unpriceable_position_is_not_silently_marked_flat(
    session_factory: sessionmaker[Session], execution: object, cost_model: CostModel
) -> None:
    """No quote, no bar, no previous mark: the engine must record the gap as
    a risk event and decline to evaluate, NOT mark the position at its own
    entry premium and report a flat book."""
    _seed(session_factory)

    closed = run_exit_cycle(
        session_factory=session_factory,
        execution=execution,  # type: ignore[arg-type]
        cost_model=cost_model,
        current_premium=lambda row: None,
        as_of=OPENED_AT + dt.timedelta(minutes=1),
    )

    assert closed == []
    with session_factory() as session:
        assert session.query(TradeRow).count() == 0, "a fabricated exit was recorded for an unpriceable position"
        kinds = [r.kind for r in session.query(RiskEventRow).all()]
    assert "position_unpriceable" in kinds, "the pricing gap was swallowed silently"


def test_the_daily_loss_limit_can_see_an_open_loss(
    session_factory: sessionmaker[Session], cost_model: CostModel, tmp_path: Path
) -> None:
    """THE safety bug: `unrealized_pnl_paise` read bars, which never contain
    an option contract, so it returned ~0 however far underwater the position
    was. Priced from the live source it must report a real, large loss."""
    from te.data.barstore import BarStore

    _seed(session_factory)
    store = BarStore(tmp_path / "bars")  # deliberately empty, as it is live
    halved = Paise(5_000)  # premium fell 50%, a Rs 3,250 loss on 65 qty

    with session_factory() as session:
        blind = unrealized_pnl_paise(session, store=store, cost_model=cost_model, as_of=OPENED_AT)
        seeing = unrealized_pnl_paise(
            session, store=store, cost_model=cost_model, as_of=OPENED_AT, current_premium=lambda row: halved
        )

    assert int(blind) > -100_000, "precondition: the bars-only path is the blind one"
    assert int(seeing) < -300_000, f"open loss still invisible to the risk gates: {int(seeing)}p"


def test_a_stale_mark_still_allows_the_hard_time_exit_but_no_price_exit(
    session_factory: sessionmaker[Session], execution: object, cost_model: CostModel
) -> None:
    """A quote outage must not strand a position past `hard_exit_by` — but it
    equally must not fire a stop or target off a price we no longer have."""
    _seed(session_factory)

    # Cycle 1 prices successfully, well inside stop and target, and persists
    # the mark. Nothing should close.
    good_mark = Paise(9_500)
    assert (
        run_exit_cycle(
            session_factory=session_factory,
            execution=execution,  # type: ignore[arg-type]
            cost_model=cost_model,
            current_premium=lambda row: good_mark,
            as_of=OPENED_AT + dt.timedelta(minutes=1),
        )
        == []
    )
    with session_factory() as session:
        assert session.query(OpenPositionRow).one().last_mark_paise == int(good_mark)

    # Cycle 2: quote fails, and it is now past the 15:20 hard exit. The
    # position must close, at the last REAL price — never at entry.
    past_hard_exit = dt.datetime(2026, 7, 31, 9, 55, tzinfo=dt.UTC)  # 15:25 IST
    closed = run_exit_cycle(
        session_factory=session_factory,
        execution=execution,  # type: ignore[arg-type]
        cost_model=cost_model,
        current_premium=lambda row: None,
        as_of=past_hard_exit,
    )

    assert closed == ["c1"]
    with session_factory() as session:
        trade = session.query(TradeRow).one()
    assert trade.exit_reason == "time"
    assert trade.exit_premium_paise == int(good_mark), "closed at a fabricated price rather than the last real mark"


def test_a_pricing_error_on_one_position_does_not_block_the_others(
    session_factory: sessionmaker[Session], execution: object, cost_model: CostModel
) -> None:
    """`quotes()` can raise something other than `OpenAlgoRestError` (a
    `KeyError` on a malformed envelope, say). One bad symbol must not unwind
    the whole exit cycle and leave every other position unmanaged."""
    _seed(session_factory)

    def _explode(row: OpenPositionRow) -> Paise | None:
        raise KeyError("data")

    closed = run_exit_cycle(
        session_factory=session_factory,
        execution=execution,  # type: ignore[arg-type]
        cost_model=cost_model,
        current_premium=_explode,
        as_of=OPENED_AT + dt.timedelta(minutes=1),
    )

    assert closed == []  # survived rather than propagating
    with session_factory() as session:
        assert "position_unpriceable" in [r.kind for r in session.query(RiskEventRow).all()]
