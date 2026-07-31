"""Regression for the defect found live on 2026-07-31: the engine ordered
the raw INDEX symbol (`BUY NIFTY on NFO` at a "premium" of ₹24,350 — the
index level) because nothing ever turned a `long_call`/`long_put` intent into
an option contract.

The whole 569-test suite passed through that bug because every other test
hand-types an option symbol (`NIFTY30JUN2626500CE`) as the configured
instrument, exercising a world the live scheduler never produces. These tests
run the cycle the way PRODUCTION does — index symbol in — and assert the
traded symbol is a genuine option contract at a genuine premium.
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
from te.domain.money import Paise
from te.domain.signal import Direction
from te.domain.symbols import parse_option_symbol
from te.engine.contract import ResolvedContract
from te.engine.cycle import CycleConfig, run_entry_cycle
from te.execution.manager import ExecutionManager
from te.execution.store import OrderEventStore
from te.persistence.db import make_engine, make_session_factory
from te.persistence.models import Base, OpenPositionRow, SkippedSignalRow, TradeRow
from te.risk.limits import RiskLimitsConfig

_CHARGES_PATH = Path(__file__).resolve().parents[2] / "config" / "charges.yaml"

# The PRODUCTION shape: the configured instrument is the bare index.
UNDERLYING = "NIFTY"
INDEX_EXCHANGE = "NFO"
ON = dt.date(2026, 7, 29)

# A real contract, as OpenAlgo's `optionsymbol` would return it. Premium and
# lot size are the live values pulled from the running instance on 2026-07-31
# (NIFTY spot 24,358.9, ATM 24350CE @ ₹96.55, lot 65).
OPTION_SYMBOL = "NIFTY04AUG2624350CE"
OPTION_EXCHANGE = "NFO"
OPTION_PREMIUM = Paise(9_655)
#: The ask is what a BUY actually pays, so it — not the LTP — is the entry
#: fill the engine must record. Half a rupee wide here, matching the ~0.1-0.4%
#: spreads the live NIFTY chain showed through OTM5 on 2026-07-31.
OPTION_ASK = Paise(9_660)
OPTION_LOT_SIZE = 65


class _NoLimiter:
    def acquire(self, n: int = 1) -> None:
        pass


def _open(minute: int) -> dt.datetime:
    return dt.datetime(2026, 7, 29, 9, 15, tzinfo=IST) + dt.timedelta(minutes=minute)


def _index_bar(event_ts: dt.datetime, *, o: float, h: float, low: float, c: float, v: int) -> dict[str, object]:
    """An INDEX bar — values at index scale (24,000+), which is exactly what
    the live WS recorder writes and what made the old code compute a ₹15.8
    lakh notional."""
    return {
        "symbol": UNDERLYING,
        "exchange": INDEX_EXCHANGE,
        "event_ts": event_ts,
        "interval": "1m",
        "o": o,
        "h": h,
        "l": low,
        "c": c,
        "v": v,
        "oi": 0,
        "ingested_at": event_ts,
        "source": "test",
    }


@pytest.fixture
def cost_model() -> CostModel:
    return CostModel(select_rates(load_charge_rate_table(_CHARGES_PATH), ON))


@pytest.fixture
def session_factory(tmp_path: Path):  # noqa: ANN201
    engine = make_engine(f"sqlite:///{tmp_path / 'contract_test.db'}")
    Base.metadata.create_all(engine)
    return make_session_factory(engine)


@pytest.fixture
def execution(session_factory, cost_model: CostModel):  # noqa: ANN001, ANN201
    return ExecutionManager(
        session_factory, OrderEventStore(session_factory), SimulatedBroker(cost_model=cost_model, on=ON), _NoLimiter()
    )


@pytest.fixture
def index_store(tmp_path: Path) -> BarStore:
    """An upside breakout on the INDEX, at real index price levels."""
    store = BarStore(tmp_path / "bars")
    store.append(
        pd.DataFrame(
            [
                _index_bar(_open(0), o=24_300, h=24_320, low=24_290, c=24_300, v=1_000),
                _index_bar(_open(1), o=24_300, h=24_310, low=24_295, c=24_305, v=1_000),
                _index_bar(_open(2), o=24_305, h=24_315, low=24_300, c=24_310, v=1_000),
                _index_bar(_open(15), o=24_310, h=24_400, low=24_310, c=24_390, v=2_000),
            ],
            columns=list(BAR_COLUMNS),
        )
    )
    return store


def _config(**overrides: object) -> CycleConfig:
    defaults: dict[str, object] = {
        "mode": "paper",
        "strategy_name": "orb",
        "instruments": (UNDERLYING,),
        "exchange": INDEX_EXCHANGE,
        "lot_size": OPTION_LOT_SIZE,
        # ₹1,00,000. At ₹20,000 a 20% stop on a ₹96.55 ATM premium risks
        # ₹1,255/lot = 6.3% of the account, which a 2% risk budget cannot
        # size — a REAL constraint of the instrument at that capital, not a
        # bug. See this module's `test_risk_budget_blocks_...` below, which
        # pins that behaviour deliberately.
        "capital": Paise(10_000_000),
        "risk_budget_pct": Decimal(2),
        "min_edge_multiple": Decimal("1.2"),
        "stop_distance": Paise(700),
        "target_distance": Paise(1_500),
        "trailing_distance": Paise(300),
        "max_hold": dt.timedelta(hours=3),
        "hard_exit_by": dt.time(15, 20),
        "risk_limits": RiskLimitsConfig(
            max_daily_loss_paise=Paise(10_000_00), max_concurrent_positions=5, max_trades_per_day=20
        ),
        "stop_pct": Decimal(20),
        "target_pct": Decimal(40),
    }
    defaults.update(overrides)
    return CycleConfig(**defaults)  # type: ignore[arg-type]


def _resolver(underlying: str, direction: Direction, as_of: dt.datetime) -> ResolvedContract | None:
    assert underlying == UNDERLYING, "resolver must be handed the underlying, not a pre-built symbol"
    return ResolvedContract(
        symbol=OPTION_SYMBOL,
        exchange=OPTION_EXCHANGE,
        lot_size=OPTION_LOT_SIZE,
        premium=OPTION_PREMIUM,
        bid=Paise(9_650),
        ask=OPTION_ASK,
        underlying_ltp=24_358.9,
    )


def _rejecting_resolver(underlying: str, direction: Direction, as_of: dt.datetime) -> ResolvedContract | None:
    return None


def test_index_breakout_opens_a_position_on_a_real_option_contract(
    session_factory, execution, cost_model: CostModel, index_store: BarStore
) -> None:  # noqa: ANN001
    """THE regression. Configured instrument is `"NIFTY"` — the live shape.
    The opened position must be a parseable option contract at the option's
    premium, never the index symbol at the index level."""
    run_entry_cycle(
        session_factory=session_factory,
        store=index_store,
        execution=execution,
        cost_model=cost_model,
        config=_config(),
        as_of=_open(16),
        contract_resolver=_resolver,
    )

    with session_factory() as session:
        rows = session.query(OpenPositionRow).all()
    assert len(rows) == 1, "the index breakout should have opened exactly one option position"
    row = rows[0]

    # The symbol must be a REAL option contract, not the underlying.
    assert row.symbol != UNDERLYING
    parsed = parse_option_symbol(row.symbol)
    assert parsed.base == UNDERLYING
    assert parsed.option_type == "CE"  # upside breakout -> long_call -> CE

    # The premium must be the OPTION's, not the index level. The old bug
    # stored 2_435_000p (₹24,350); the real premium is ~9_655p (₹96.55).
    #
    # Specifically the ASK, not the LTP: this is a BUY, so the ask is what is
    # actually payable. Booking the entry at LTP handed the engine half the
    # spread as free profit on every trade.
    assert row.entry_premium_paise == int(OPTION_ASK)
    assert row.entry_premium_paise < 100_000, "premium is at index scale — the resolver was bypassed"
    assert row.lot_size == OPTION_LOT_SIZE


def test_stop_and_target_are_percentages_of_the_option_premium(
    session_factory, execution, cost_model: CostModel, index_store: BarStore
) -> None:  # noqa: ANN001
    """Absolute paise distances are index-point-scaled and mean different
    things at different premiums (a ₹15 target is 50% of a ₹30 premium and 5%
    of a ₹300 one). With `stop_pct`/`target_pct` set they must be derived
    from the resolved premium."""
    run_entry_cycle(
        session_factory=session_factory,
        store=index_store,
        execution=execution,
        cost_model=cost_model,
        config=_config(stop_pct=Decimal(20), target_pct=Decimal(40)),
        as_of=_open(16),
        contract_resolver=_resolver,
    )

    with session_factory() as session:
        row = session.query(OpenPositionRow).one()
    # Percentages are taken off the ACTUAL entry fill (the ask), so the
    # stop really is 20% of what was paid rather than 20% of a price the
    # engine never traded at.
    assert row.stop_paise == int(OPTION_ASK) - int(int(OPTION_ASK) * 20 / 100)
    assert row.target_paise == int(OPTION_ASK) + int(int(OPTION_ASK) * 40 / 100)


def test_trailing_distance_scales_with_the_option_premium(
    session_factory, execution, cost_model: CostModel, index_store: BarStore
) -> None:  # noqa: ANN001
    """Regression for a bug found live on 2026-07-31 that closed 14 of 14
    trades in an average of 3.1 minutes, none near their real stop or
    target. `stop`/`target` had been converted to percentages of premium but
    `trailing_distance` was left at its absolute ₹3 — an index-point-scaled
    leftover worth 0.44% of a ₹676 option, i.e. tighter than tick-to-tick
    noise, so the trail ratcheted to just under spot and exited on the first
    trivial pullback. Option premium is several times more volatile than the
    underlying in percentage terms, and ORB's edge is asymmetry (winners
    must be allowed to run), so an over-tight trail destroys the strategy."""
    run_entry_cycle(
        session_factory=session_factory,
        store=index_store,
        execution=execution,
        cost_model=cost_model,
        config=_config(trailing_pct=Decimal(15)),
        as_of=_open(16),
        contract_resolver=_resolver,
    )

    with session_factory() as session:
        row = session.query(OpenPositionRow).one()

    expected = int(int(OPTION_ASK) * 15 / 100)
    assert row.trailing_distance_paise == expected
    # The real point: the trail must be a meaningful fraction of premium,
    # not the ~0.4% that strangled every live trade.
    assert row.trailing_distance_paise > int(OPTION_ASK) * 5 // 100


def test_cost_gate_passes_once_the_premium_is_real(
    session_factory, execution, cost_model: CostModel, index_store: BarStore
) -> None:  # noqa: ANN001
    """The old code's rejection was not a tuning problem — at a ₹24,350
    "premium" the round-trip cost was ₹3,824 against a ₹975 edge, so the
    cost-vs-edge gate rejected deterministically at ANY `min_edge_multiple`.
    At a real ₹96.55 premium the same gate passes untouched."""
    run_entry_cycle(
        session_factory=session_factory,
        store=index_store,
        execution=execution,
        cost_model=cost_model,
        config=_config(),
        as_of=_open(16),
        contract_resolver=_resolver,
    )

    with session_factory() as session:
        skips = [s.reason for s in session.query(SkippedSignalRow).all()]
    assert not any("round-trip cost" in reason for reason in skips), (
        f"cost gate still rejecting with a real premium: {skips}"
    )


def test_risk_budget_blocks_a_nifty_lot_at_20k_capital_and_says_so(
    session_factory, execution, cost_model: CostModel, index_store: BarStore
) -> None:  # noqa: ANN001
    """Pins a REAL capital constraint, verified against the live chain on
    2026-07-31: at ₹20,000 capital with a 2% risk budget, one NIFTY ATM lot
    (₹96.55 x 65 = ₹6,276) with a 20% premium stop risks ₹1,255 — over three
    times the ₹400 budget. The engine must refuse it and state the
    arithmetic, not size a position that breaches the stated risk budget.

    This is the honest constraint behind "₹20k is thin for NIFTY": not that
    the lot is unaffordable (it is 31% of the account), but that a sane
    option stop cannot fit inside a sane risk budget at that capital."""
    run_entry_cycle(
        session_factory=session_factory,
        store=index_store,
        execution=execution,
        cost_model=cost_model,
        config=_config(capital=Paise(2_000_000)),  # ₹20,000
        as_of=_open(16),
        contract_resolver=_resolver,
    )

    with session_factory() as session:
        assert session.query(OpenPositionRow).count() == 0
        reasons = [s.reason for s in session.query(SkippedSignalRow).all()]
    assert any("risk budget" in reason for reason in reasons), reasons


def test_a_stopped_out_position_does_not_reenter_the_same_underlying_same_day(
    session_factory, execution, cost_model: CostModel, index_store: BarStore
) -> None:  # noqa: ANN001
    """The max-entries-per-session RISK cap (distinct from the edge-trigger
    correctness fix in `te.strategy.orb`). Set to 1 here so a single prior
    entry exhausts it; the shipped default is 2, matching the ORB
    literature's "one or two per session"."""
    config = _config(max_entries_per_underlying_per_day=1)

    cycle_id_1 = run_entry_cycle(
        session_factory=session_factory,
        store=index_store,
        execution=execution,
        cost_model=cost_model,
        config=config,
        as_of=_open(16),
        contract_resolver=_resolver,
    )
    with session_factory() as session:
        row = session.query(OpenPositionRow).one()
        # Simulate the position having been stopped out before the next
        # cycle runs — the bug reproduces on a CLOSED position, not just an
        # open one (an open one is already covered by
        # `check_max_concurrent_positions`). Mirrors `_close_position`: both
        # the position row AND a `TradeRow` are required for a real close.
        closed_at = _open(17).astimezone(dt.UTC)
        row.closed_at = closed_at
        session.add(
            TradeRow(
                client_order_id=row.client_order_id,
                symbol=row.symbol,
                exchange=row.exchange,
                strategy=row.strategy,
                direction=row.direction,
                lots=row.lots,
                lot_size=row.lot_size,
                entry_premium_paise=row.entry_premium_paise,
                exit_premium_paise=row.stop_paise,
                gross_pnl_paise=row.stop_paise - row.entry_premium_paise,
                costs_paise=0,
                net_pnl_paise=row.stop_paise - row.entry_premium_paise,
                exit_reason="stop",
                mode="paper",
                opened_at=row.opened_at,
                closed_at=closed_at,
            )
        )
        session.commit()

    cycle_id_2 = run_entry_cycle(
        session_factory=session_factory,
        store=index_store,
        execution=execution,
        cost_model=cost_model,
        config=config,
        as_of=_open(18),
        contract_resolver=_resolver,
    )
    assert cycle_id_2 != cycle_id_1

    with session_factory() as session:
        open_count = session.query(OpenPositionRow).filter(OpenPositionRow.closed_at.is_(None)).count()
        reasons = [
            s.reason for s in session.query(SkippedSignalRow).filter(SkippedSignalRow.instrument == UNDERLYING)
        ]
    assert open_count == 0, "must not have re-entered — the earlier position was closed, not re-opened"
    assert any("per-session limit" in r for r in reasons), reasons


def test_unresolvable_contract_skips_with_a_real_reason_instead_of_trading_the_index(
    session_factory, execution, cost_model: CostModel, index_store: BarStore
) -> None:  # noqa: ANN001
    """A broker outage or a guard rejection must degrade to a recorded skip —
    never to falling back to trading the underlying, and never to a crash."""
    run_entry_cycle(
        session_factory=session_factory,
        store=index_store,
        execution=execution,
        cost_model=cost_model,
        config=_config(),
        as_of=_open(16),
        contract_resolver=_rejecting_resolver,
    )

    with session_factory() as session:
        assert session.query(OpenPositionRow).count() == 0
        reasons = [s.reason for s in session.query(SkippedSignalRow).all()]
    assert any("option contract" in reason for reason in reasons), reasons
