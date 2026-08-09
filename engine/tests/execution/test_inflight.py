"""te.execution.inflight.resolve_inflight — timeout -> query, NEVER blind
retry. Covers both branches of the R1 resolution: a broker that supports
`query_order(client_order_id)` directly, and (the path this project's
research concluded OpenAlgo actually needs) one that raises
`NotImplementedError` and must be resolved via `order_reports()` window
matching instead."""

from __future__ import annotations

import datetime as dt

from te.broker.protocol import OrderStatusReport
from te.domain.orders import Order
from te.execution.inflight import match_pending_order, resolve_inflight

TS = dt.datetime(2026, 7, 29, 9, 20, tzinfo=dt.UTC)


def _order(**overrides: object) -> Order:
    base = {
        "client_order_id": "coid-1",
        "symbol": "NIFTY30JUN2626500CE",
        "exchange": "NFO",
        "side": "BUY",
        "quantity": 65,
        "status": "INITIALIZED",
    }
    base.update(overrides)
    return Order(**base)  # type: ignore[arg-type]


def _report(**overrides: object) -> OrderStatusReport:
    base = {
        "client_order_id": None,
        "venue_order_id": "v-1",
        "symbol": "NIFTY30JUN2626500CE",
        "exchange": "NFO",
        "side": "BUY",
        "quantity": 65,
        "filled_qty": 65,
        "status": "complete",
        "ts": TS,
    }
    base.update(overrides)
    return OrderStatusReport(**base)  # type: ignore[arg-type]


class _DirectQueryBroker:
    """Supports client-tag lookup directly (the primary design, if a
    broker supported it)."""

    def __init__(self, report: OrderStatusReport | None) -> None:
        self._report = report
        self.query_calls = 0
        self.order_reports_calls = 0

    def query_order(self, client_order_id: str) -> OrderStatusReport | None:
        self.query_calls += 1
        return self._report

    def order_reports(self) -> list[OrderStatusReport]:
        self.order_reports_calls += 1
        return []


class _FallbackOnlyBroker:
    """The path R1 concluded OpenAlgo actually needs: no client-tag lookup
    (`query_order` raises `NotImplementedError`), so `resolve_inflight` must
    fall back to matching `order_reports()` by (symbol, side, qty, window)."""

    def __init__(self, reports: list[OrderStatusReport]) -> None:
        self._reports = reports
        self.query_calls = 0
        self.order_reports_calls = 0

    def query_order(self, client_order_id: str) -> OrderStatusReport | None:
        self.query_calls += 1
        raise NotImplementedError

    def order_reports(self) -> list[OrderStatusReport]:
        self.order_reports_calls += 1
        return self._reports


def test_match_pending_order_matches_unique_candidate() -> None:
    candidates = [_report(ts=TS + dt.timedelta(seconds=1))]
    match = match_pending_order(
        candidates,
        symbol="NIFTY30JUN2626500CE",
        side="BUY",
        quantity=65,
        submitted_at=TS,
        window=dt.timedelta(seconds=5),
    )
    assert match is not None
    assert match.venue_order_id == "v-1"


def test_match_pending_order_rejects_outside_window() -> None:
    candidates = [_report(ts=TS + dt.timedelta(seconds=30))]
    match = match_pending_order(
        candidates,
        symbol="NIFTY30JUN2626500CE",
        side="BUY",
        quantity=65,
        submitted_at=TS,
        window=dt.timedelta(seconds=5),
    )
    assert match is None


def test_match_pending_order_rejects_wrong_symbol() -> None:
    """A candidate inside the window that differs ONLY in symbol must not
    match — otherwise an in-flight order resolves against an unrelated
    contract placed in the same 5-second window."""
    candidates = [_report(symbol="BANKNIFTY30JUN2652500CE", ts=TS + dt.timedelta(seconds=1))]
    match = match_pending_order(
        candidates,
        symbol="NIFTY30JUN2626500CE",
        side="BUY",
        quantity=65,
        submitted_at=TS,
        window=dt.timedelta(seconds=5),
    )
    assert match is None


def test_match_pending_order_rejects_wrong_side() -> None:
    """A candidate inside the window that differs ONLY in side must not
    match — otherwise a BUY resolves against an unrelated SELL."""
    candidates = [_report(side="SELL", ts=TS + dt.timedelta(seconds=1))]
    match = match_pending_order(
        candidates,
        symbol="NIFTY30JUN2626500CE",
        side="BUY",
        quantity=65,
        submitted_at=TS,
        window=dt.timedelta(seconds=5),
    )
    assert match is None


def test_match_pending_order_rejects_wrong_quantity() -> None:
    """A candidate inside the window that differs ONLY in quantity must not
    match — otherwise a 65-lot order resolves against an unrelated 195-lot
    order."""
    candidates = [_report(quantity=195, ts=TS + dt.timedelta(seconds=1))]
    match = match_pending_order(
        candidates,
        symbol="NIFTY30JUN2626500CE",
        side="BUY",
        quantity=65,
        submitted_at=TS,
        window=dt.timedelta(seconds=5),
    )
    assert match is None


def test_match_pending_order_ambiguous_is_none() -> None:
    candidates = [
        _report(venue_order_id="v-1", ts=TS + dt.timedelta(seconds=1)),
        _report(venue_order_id="v-2", ts=TS + dt.timedelta(seconds=2)),
    ]
    match = match_pending_order(
        candidates,
        symbol="NIFTY30JUN2626500CE",
        side="BUY",
        quantity=65,
        submitted_at=TS,
        window=dt.timedelta(seconds=5),
    )
    assert match is None  # ambiguous -> caller must treat as unresolved, never guess


def test_resolve_inflight_uses_direct_query_when_supported() -> None:
    report = _report()
    broker = _DirectQueryBroker(report)
    result = resolve_inflight(_order(), TS, broker)
    assert result is report
    assert broker.query_calls == 1
    assert broker.order_reports_calls == 0


def test_inflight_resolution_uses_query_path() -> None:
    """The load-bearing R1 test: when the broker cannot look orders up by
    OUR id (the path this project's research concluded OpenAlgo needs),
    resolve_inflight falls back to order_reports() + window matching."""
    report = _report(ts=TS + dt.timedelta(seconds=1))
    broker = _FallbackOnlyBroker([report])
    result = resolve_inflight(_order(), TS, broker)
    assert result is report
    assert broker.query_calls == 1
    assert broker.order_reports_calls == 1


def test_resolve_inflight_returns_none_when_unresolvable() -> None:
    broker = _FallbackOnlyBroker([])
    result = resolve_inflight(_order(), TS, broker)
    assert result is None
