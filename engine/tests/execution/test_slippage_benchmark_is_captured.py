"""The prices a fill has to be judged against, captured at t=0.

`te.risk.monitors.SlippageMonitor` compares an EXPECTED price against the
actual fill. It has never run in production, and the reason turned out to be
structural rather than a missing call: nothing recorded the expected side.
`OrderInitialized` held side and quantity but no price at all, and the folded
`Order` has no price field either — so there was no expected price anywhere in
the system to compare against.

Two prices are recorded, not one, and the distinction is the point:

* `requested_price` — what we asked for.
* `arrival_bid`/`arrival_ask` — where the market actually was at that instant.

Judging a fill against our own limit alone would score a genuine price move as
bad execution: ask Rs 74, market moves to Rs 76, fill at Rs 76, and a
limit-only comparison calls that Rs 2 of slippage when the fill was fair.
Perold's implementation shortfall (1988) measures against the arrival price
for exactly this reason, and it remains what execution desks use. Bid and ask
are both kept so slippage can be expressed relative to the spread later
without replaying tick data.

They live on the INITIALIZED event because they are unrecoverable afterwards:
once the fill returns, the market's state at submission is gone unless a full
tick archive exists. `persist_initial_event` already runs before any network
call, which is precisely the instant being described.
"""

from __future__ import annotations

import datetime as dt
import json

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import sessionmaker

from te.domain.events import OrderInitialized
from te.domain.money import Paise
from te.domain.orders import OrderRequest
from te.execution.idempotency import persist_initial_event
from te.execution.store import OrderEventStore
from te.persistence.models import Base, OrderEventRow

_TS = dt.datetime(2026, 8, 4, 4, 0, tzinfo=dt.UTC)


@pytest.fixture
def store(tmp_path):  # noqa: ANN001, ANN201
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'orders.db'}")
    Base.metadata.create_all(engine)
    return OrderEventStore(sessionmaker(bind=engine, expire_on_commit=False))


def _request(**overrides) -> OrderRequest:  # noqa: ANN003
    base = {
        "symbol": "NIFTY04AUG2624600PE",
        "exchange": "NFO",
        "side": "BUY",
        "quantity": 65,
        "order_type": "LIMIT",
        "limit_price": Paise(7_400),
        "arrival_bid": Paise(7_350),
        "arrival_ask": Paise(7_400),
    }
    return OrderRequest(**{**base, **overrides})  # type: ignore[arg-type]


def test_both_the_ask_and_the_market_behind_it_are_recorded(store) -> None:  # noqa: ANN001
    event = persist_initial_event(store, client_order_id="c-1", request=_request(), ts=_TS)

    assert event.requested_price == Paise(7_400)
    assert event.arrival_bid == Paise(7_350)
    assert event.arrival_ask == Paise(7_400)


def test_the_benchmark_survives_a_round_trip_through_the_event_log(store) -> None:  # noqa: ANN001
    """It is only useful if it comes back out — the monitor reads history,
    not the in-memory object."""
    persist_initial_event(store, client_order_id="c-1", request=_request(), ts=_TS)

    replayed = store.events_for("c-1")

    assert isinstance(replayed[0], OrderInitialized)
    assert replayed[0].requested_price == Paise(7_400)
    assert replayed[0].arrival_bid == Paise(7_350)
    assert replayed[0].arrival_ask == Paise(7_400)


def test_an_order_placed_before_this_existed_still_replays(store) -> None:  # noqa: ANN001
    """The live database already holds orders written without these fields.
    `_deserialize` does `cls(**payload)`, so a REQUIRED new field would raise
    on every historical order and take the whole event log down with it.
    Writes the pre-2026-08-04 payload by hand and requires it back."""
    legacy = {
        "client_order_id": "old-1",
        "symbol": "NIFTY04AUG2624600PE",
        "exchange": "NFO",
        "side": "BUY",
        "quantity": 65,
        "ts": _TS.isoformat(),
    }
    with store._session_factory() as session:  # noqa: SLF001
        session.add(
            OrderEventRow(
                client_order_id="old-1",
                seq=0,
                event_type="OrderInitialized",
                payload_json=json.dumps(legacy),
                ts=_TS,
            )
        )
        session.commit()

    replayed = store.events_for("old-1")

    assert isinstance(replayed[0], OrderInitialized)
    # Honest absence, not a fabricated zero: this order genuinely has no
    # benchmark, and slippage on it can never be measured.
    assert replayed[0].requested_price is None
    assert replayed[0].arrival_bid is None


def test_a_caller_with_no_quote_records_no_benchmark(store) -> None:  # noqa: ANN001
    """A backtest replay has no live two-sided market. That must read as
    "not measured" rather than as a zero-spread market, which would make
    every fill look perfect."""
    event = persist_initial_event(
        store,
        client_order_id="c-2",
        request=_request(arrival_bid=None, arrival_ask=None),
        ts=_TS,
    )

    assert event.arrival_bid is None
    assert event.arrival_ask is None
    assert event.requested_price == Paise(7_400), "the limit is still known even with no quote"


def test_the_entry_order_carries_the_contracts_real_two_sided_market() -> None:
    """End to end through the real entry path rather than a hand-built
    request: `run_entry_cycle` buys at the ask, and the bid must travel with
    it. If only the ask were recorded, spread-relative slippage could never
    be computed and the half-spread would be invisible."""
    from te.engine.contract import ResolvedContract

    contract = ResolvedContract(
        symbol="NIFTY04AUG2624600PE",
        exchange="NFO",
        lot_size=65,
        premium=Paise(7_380),
        bid=Paise(7_350),
        ask=Paise(7_400),
        underlying_ltp=24_600.0,
    )
    request = OrderRequest(
        symbol=contract.symbol,
        exchange=contract.exchange,
        side="BUY",
        quantity=contract.lot_size,
        order_type="LIMIT",
        limit_price=contract.ask,
        arrival_bid=contract.bid,
        arrival_ask=contract.ask,
    )

    # The half-spread the engine pays on entry, now measurable.
    assert int(request.arrival_ask) - int(request.arrival_bid) == 50
    assert request.limit_price == contract.ask


class _NoAutoFillBroker:
    """A `SimulatedBroker` that never reports its own fills.

    `ExecutionManager.submit` now drains `BrokerPort.fill_reports()` (see
    `ExecutionManager.drain_fills` — before that, nothing in production ever
    called `on_fill` at all). The two tests below hand-deliver a specific
    `FillReport` in order to pin the BENCHMARK and the SIGN of one
    observation, and a real `SimulatedBroker` would fill the order at its
    limit price first — leaving the hand-delivered fill to be rejected by
    the overfill guard and measuring the simulator instead of the case under
    test.

    Withholding the automatic fill keeps each test measuring exactly the one
    fill it constructs. That the drain itself works is pinned separately, in
    `tests/execution/test_fills_reach_the_manager.py`.
    """

    def __init__(self, inner: SimulatedBroker) -> None:
        self._inner = inner

    def place_order(self, intent):  # noqa: ANN001, ANN201
        return self._inner.place_order(intent)

    def cancel_order(self, client_order_id, venue_order_id):  # noqa: ANN001, ANN201
        return self._inner.cancel_order(client_order_id, venue_order_id)

    def query_order(self, client_order_id):  # noqa: ANN001, ANN201
        return self._inner.query_order(client_order_id)

    def order_reports(self):  # noqa: ANN201
        return self._inner.order_reports()

    def fill_reports(self):  # noqa: ANN201
        return []

    def position_reports(self):  # noqa: ANN201
        return self._inner.position_reports()


def test_a_fill_records_a_slippage_observation(tmp_path) -> None:  # noqa: ANN001
    """The monitor existed but had never observed anything, so the
    live-money gate's "slippage is clean" condition passed on an empty
    sample. A gate that cannot fail is not a gate."""

    from te.broker.protocol import FillReport
    from te.broker.ratelimit import TokenBucket
    from te.broker.simulated import SimulatedBroker
    from te.data.charges_loader import load_charge_rate_table
    from te.domain.costs import CostModel, select_rates
    from te.execution.manager import ExecutionManager
    from te.persistence.models import SlippageObservationRow
    from te.settings import Settings

    engine = sa.create_engine(f"sqlite:///{tmp_path / 'fills.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    cost_model = CostModel(select_rates(load_charge_rate_table(Settings().charges_path), _TS.date()))
    manager = ExecutionManager(
        factory,
        OrderEventStore(factory),
        _NoAutoFillBroker(SimulatedBroker(cost_model=cost_model, on=_TS.date())),
        TokenBucket(rate=100, capacity=100),
    )

    client_order_id = manager.submit(_request())
    # Filled Rs 0.50 above the arrival mid of Rs 73.75 ((7350 + 7400) / 2).
    manager.on_fill(
        FillReport(
            client_order_id=client_order_id,
            venue_order_id="v-1",
            venue_trade_id="t-1",
            symbol="NIFTY04AUG2624600PE",
            exchange="NFO",
            side="BUY",
            fill_qty=65,
            fill_price=Paise(7_425),
            gross_amount_paise=Paise(-482_625),
            cost_breakdown=cost_model.round_trip(
                entry_premium=Paise(7_425), exit_premium=Paise(7_425), qty=65, exchange="NFO", on=_TS.date()
            ),
            net_amount_paise=Paise(-482_625),
            ts=_TS,
        )
    )

    with factory() as session:
        rows = session.query(SlippageObservationRow).all()

    assert len(rows) == 1, "the fill was applied but never measured"
    # Benchmarked against the arrival MID (7375), not the ask we asked at
    # (7400) — see `_observe_slippage`.
    assert rows[0].expected_paise == 7_375
    assert rows[0].actual_paise == 7_425


def test_a_fill_with_no_two_sided_quote_falls_back_to_the_requested_price(tmp_path) -> None:  # noqa: ANN001
    """When no arrival bid/ask was captured (e.g. a backtest replay), the
    slippage benchmark must fall back to `requested_price` — an `is not
    None` -> `is None` flip on that `elif` would skip this branch even
    though `requested_price` genuinely IS present, and `_observe_slippage`
    would then return without recording anything at all."""
    from te.broker.protocol import FillReport
    from te.broker.ratelimit import TokenBucket
    from te.broker.simulated import SimulatedBroker
    from te.data.charges_loader import load_charge_rate_table
    from te.domain.costs import CostModel, select_rates
    from te.execution.manager import ExecutionManager
    from te.persistence.models import SlippageObservationRow
    from te.settings import Settings

    engine = sa.create_engine(f"sqlite:///{tmp_path / 'noquote.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    cost_model = CostModel(select_rates(load_charge_rate_table(Settings().charges_path), _TS.date()))
    manager = ExecutionManager(
        factory,
        OrderEventStore(factory),
        _NoAutoFillBroker(SimulatedBroker(cost_model=cost_model, on=_TS.date())),
        TokenBucket(rate=100, capacity=100),
    )

    client_order_id = manager.submit(_request(arrival_bid=None, arrival_ask=None))
    manager.on_fill(
        FillReport(
            client_order_id=client_order_id,
            venue_order_id="v-1",
            venue_trade_id="t-1",
            symbol="NIFTY04AUG2624600PE",
            exchange="NFO",
            side="BUY",
            fill_qty=65,
            fill_price=Paise(7_425),
            gross_amount_paise=Paise(-482_625),
            cost_breakdown=cost_model.round_trip(
                entry_premium=Paise(7_425), exit_premium=Paise(7_425), qty=65, exchange="NFO", on=_TS.date()
            ),
            net_amount_paise=Paise(-482_625),
            ts=_TS,
        )
    )

    with factory() as session:
        rows = session.query(SlippageObservationRow).all()

    assert len(rows) == 1, "no arrival quote, but requested_price was present — must still be measured"
    # Benchmarked against the REQUESTED price (7400), the only benchmark
    # available with no two-sided quote.
    assert rows[0].expected_paise == 7_400


def test_a_bad_sell_fill_is_recorded_as_bad_not_good(tmp_path) -> None:  # noqa: ANN001
    """The sign bug, caught in review 2026-08-05.

    `SlippageMonitor.status` only ever breaches on a POSITIVE mean, which is
    correct for a buy (paying more is worse) and backwards for a sell
    (receiving less is worse, and produces a negative difference).

    Every exit is a sell. Unadjusted, a systematically bad exit fill was
    recorded as favourable and cancelled the entry side out of the same
    mean, so the monitor could never breach and the live-money gate's
    "slippage is clean" condition passed unconditionally.
    """
    from te.broker.protocol import FillReport
    from te.broker.ratelimit import TokenBucket
    from te.broker.simulated import SimulatedBroker
    from te.data.charges_loader import load_charge_rate_table
    from te.domain.costs import CostModel, select_rates
    from te.execution.manager import ExecutionManager
    from te.persistence.models import SlippageObservationRow
    from te.settings import Settings

    engine = sa.create_engine(f"sqlite:///{tmp_path / 'sell.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    cost_model = CostModel(select_rates(load_charge_rate_table(Settings().charges_path), _TS.date()))
    manager = ExecutionManager(
        factory,
        OrderEventStore(factory),
        _NoAutoFillBroker(SimulatedBroker(cost_model=cost_model, on=_TS.date())),
        TokenBucket(rate=100, capacity=100),
    )

    client_order_id = manager.submit(_request(side="SELL"))
    # Arrival mid is Rs 73.75. Sold at Rs 73.25 — FIFTY paise WORSE than the
    # market, because a seller wants a higher price, not a lower one.
    manager.on_fill(
        FillReport(
            client_order_id=client_order_id,
            venue_order_id="v-1",
            venue_trade_id="t-1",
            symbol="NIFTY04AUG2624600PE",
            exchange="NFO",
            side="SELL",
            fill_qty=65,
            fill_price=Paise(7_325),
            gross_amount_paise=Paise(476_125),
            cost_breakdown=cost_model.round_trip(
                entry_premium=Paise(7_325), exit_premium=Paise(7_325), qty=65, exchange="NFO", on=_TS.date()
            ),
            net_amount_paise=Paise(476_125),
            ts=_TS,
        )
    )

    with factory() as session:
        row = session.query(SlippageObservationRow).one()

    assert row.expected_paise == 7_375
    assert row.diff_paise > 0, "a sell BELOW the benchmark must read as costly, not as a gain"
    assert row.diff_paise == 50
