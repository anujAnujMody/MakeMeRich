"""Proves the Tier-0 fix from the "Dashboard<->engine wiring remediation"
plan section: `dashboard`/`pnl`/`trades`/`positions`/`orders` used to be
literal Phase-0 stubs (hardcoded zeros/empty lists) never repointed at the
real `trades`/`open_positions`/`order_events` tables Phase 3/4's execution
core actually writes to. Seeds real rows directly into an isolated DB and
asserts each endpoint reflects them — not just that the endpoint doesn't
crash, which a schema-shape contract test alone can't distinguish from "the
router still returns a hardcoded empty stub that happens to validate"."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import te.api.routers.dashboard as dashboard_router
import te.api.routers.engine as engine_router
import te.api.routers.execution as execution_router
import te.api.routers.market as market_router
import te.api.routers.orders as orders_router
import te.api.routers.pnl as pnl_router
import te.api.routers.positions as positions_router
import te.api.routers.trades as trades_router
from te.domain.clock import IST
from te.domain.events import OrderAccepted, OrderFilled, OrderInitialized
from te.domain.money import Paise
from te.execution.store import OrderEventStore
from te.persistence.db import make_engine, make_session_factory
from te.persistence.models import Base, OpenPositionRow, TradeRow
from te.persistence.repos.paper_trading import record_skipped_signal

# The routers under test compute "today" via `dt.datetime.now(IST).date()`
# directly (no injectable clock yet), and `_day_bounds()` (paper_trading.py)
# treats that IST calendar date as if it were a UTC date (`[on 00:00 UTC, on
# 23:59:59 UTC]`) — so seeded rows must use THAT SAME date's Y/M/D directly
# in a UTC-tagged datetime, not an astimezone() conversion (which can shift
# the date backward across the UTC/IST offset and silently land outside the
# window the router queries).
_IST_TODAY = dt.datetime.now(IST).date()
NOW = dt.datetime(_IST_TODAY.year, _IST_TODAY.month, _IST_TODAY.day, 10, 0, tzinfo=dt.UTC)
TODAY = NOW.date()


@pytest.fixture
def isolated_client(tmp_path: Path, monkeypatch):  # noqa: ANN201
    engine = make_engine(f"sqlite:///{tmp_path / 'tier0_wiring_test.db'}")
    Base.metadata.create_all(engine)
    sf = make_session_factory(engine)
    for module in (
        dashboard_router,
        pnl_router,
        trades_router,
        positions_router,
        orders_router,
        engine_router,
        execution_router,
        market_router,
    ):
        monkeypatch.setattr(module, "session_factory", sf)

    from te.api.main import app

    return TestClient(app), sf


def _seed_closed_trade(sf, *, net_pnl_paise: int = 5_000) -> None:  # noqa: ANN001
    with sf() as session:
        # A real closed trade always has a matching ENTRY-ledger row too —
        # `open_positions` is written at open and never deleted, `closed_at`
        # stamped on close (see CLAUDE.md's "Two ledgers"). Seeding only the
        # `TradeRow` (as this fixture used to) understates
        # `entries_count_today`, which reads `open_positions` alone.
        session.add(
            OpenPositionRow(
                client_order_id="co-1",
                symbol="NIFTY30JUL2624500CE",
                exchange="NFO",
                strategy="orb",
                direction="long_call",
                lots=1,
                lot_size=65,
                entry_premium_paise=10_000,
                stop_paise=9_000,
                current_stop_paise=9_000,
                trailing_distance_paise=None,
                target_paise=11_000,
                max_hold_seconds=10_800,
                hard_exit_by="15:20:00",
                opened_at=NOW - dt.timedelta(minutes=30),
                closed_at=NOW,
            )
        )
        session.add(
            TradeRow(
                client_order_id="co-1",
                symbol="NIFTY30JUL2624500CE",
                exchange="NFO",
                strategy="orb",
                direction="long_call",
                lots=1,
                lot_size=65,
                entry_premium_paise=10_000,
                exit_premium_paise=10_500,
                gross_pnl_paise=net_pnl_paise + 100,
                costs_paise=100,
                net_pnl_paise=net_pnl_paise,
                exit_reason="target",
                mode="paper",
                opened_at=NOW - dt.timedelta(minutes=30),
                closed_at=NOW,
            )
        )
        session.commit()


def _seed_open_position(sf) -> None:  # noqa: ANN001
    with sf() as session:
        session.add(
            OpenPositionRow(
                client_order_id="co-2",
                symbol="BANKNIFTY30JUL2652000PE",
                exchange="NFO",
                strategy="orb",
                direction="long_put",
                lots=2,
                lot_size=30,
                entry_premium_paise=8_000,
                stop_paise=7_000,
                current_stop_paise=7_000,
                trailing_distance_paise=None,
                target_paise=10_000,
                max_hold_seconds=10_800,
                hard_exit_by="15:20:00",
                opened_at=NOW,
                closed_at=None,
            )
        )
        session.commit()


def test_dashboard_reflects_real_closed_trade_and_open_position(isolated_client) -> None:  # noqa: ANN001
    client, sf = isolated_client
    _seed_closed_trade(sf)
    _seed_open_position(sf)

    body = client.get("/api/dashboard").json()
    assert body["totalTrades"] == 1
    assert body["dayPnl"] == 50.0  # 5_000 paise net -> Rs 50
    assert body["activePositions"] == 1
    assert body["positions"][0]["symbol"] == "BANKNIFTY30JUL2652000PE"


def test_dashboard_snapshot_reflects_real_mode_and_counts(isolated_client) -> None:  # noqa: ANN001
    """`tradesToday` is rendered against `maxTradesPerDay` in the same
    response body — that cap is enforced on the ENTRY ledger
    (`entries_count_today`), so the seeded 1 closed trade + 1 open position
    must read as 2, not 1 (the exact pair that distinguishes the two
    ledgers). Likewise `todayPnl` is displayed beside `dailyLossLimit`,
    which `check_daily_loss_limit` enforces on realized PLUS unrealized
    P&L — see `test_dashboard_snapshot_today_pnl_moves_with_unrealized_mark`
    for the case where that unrealized term actually changes the number."""
    client, sf = isolated_client
    _seed_closed_trade(sf)
    _seed_open_position(sf)

    body = client.get("/api/dashboard/snapshot").json()
    assert body["tradesToday"] == 2
    assert body["openPositionsCount"] == 1
    # The open position has no recorded bar yet, so it marks at its own
    # entry premium (`latest_close_paise(..., fallback=entry_premium)`) —
    # zero GROSS unrealized P&L, but a real round-trip exit cost is still
    # incurred even at a flat mark, so `todayPnl` must be strictly below the
    # Rs 50 realized-only figure (this is what proves the unrealized term is
    # actually wired in, not merely present and always zero).
    assert body["todayPnl"] < 50.0

    from te.api.db import charge_rate_table
    from te.domain.costs import CostModel, select_rates
    from te.domain.pnl import mark_to_market_pnl

    today = dt.datetime.now(IST).date()
    expected_unrealized_paise = mark_to_market_pnl(
        entry_premium=Paise(8_000),
        current_premium=Paise(8_000),
        qty=60,  # 2 lots x 30 lot_size, matching `_seed_open_position`
        exchange="NFO",
        cost_model=CostModel(select_rates(charge_rate_table, today)),
        on=today,
    )
    expected_today_pnl = round(50.0 + float(int(expected_unrealized_paise)) / 100, 2)
    assert body["todayPnl"] == expected_today_pnl
    assert body["mode"] == "dry-run"  # real default, read from engine_state


def test_dashboard_snapshot_today_pnl_moves_with_unrealized_mark(isolated_client, tmp_path, monkeypatch) -> None:  # noqa: ANN001
    """Regression for the dashboard agreeing with the counter bug: with a
    position open and underwater, `todayPnl` must include that unrealized
    loss — otherwise the page shows less budget used than
    `check_daily_loss_limit` (realized + unrealized) is actually enforcing."""
    import pandas as pd

    from te.data.barstore import BAR_COLUMNS, BarStore

    client, sf = isolated_client
    _seed_closed_trade(sf, net_pnl_paise=0)
    _seed_open_position(sf)  # entry_premium_paise=8_000, 2 lots x 30 lot_size = 60 qty

    # The dashboard router marks positions off the REAL wall clock
    # (`dt.datetime.now(IST)`), not this module's fixed `NOW` fixture — so
    # the bar must be recent relative to actual test-run time, well inside
    # `latest_close_paise`'s default 5-minute lookback.
    bar_open = dt.datetime.now(dt.UTC) - dt.timedelta(minutes=2)
    row = {
        "symbol": "BANKNIFTY30JUL2652000PE",
        "exchange": "NFO",
        "event_ts": bar_open,
        "interval": "1m",
        "o": 80.0,
        "h": 80.0,
        "l": 60.0,
        "c": 60.0,  # Rs 60, well below the Rs 80 entry premium -> a real unrealized loss
        "v": 1,
        "oi": 0,
        "ingested_at": bar_open + dt.timedelta(minutes=1),
        "source": "test",
    }
    store = BarStore(tmp_path / "bars")
    store.append(pd.DataFrame([row], columns=list(BAR_COLUMNS)))
    monkeypatch.setattr(dashboard_router, "bar_store", store)

    body = client.get("/api/dashboard/snapshot").json()

    # (6_000 - 8_000) paise x 60 qty = -Rs 1,200 gross, minus real round-trip
    # costs -> strictly worse than the realized-only Rs 0.
    assert body["todayPnl"] < 0


def test_dashboard_honest_zero_state_with_no_data(isolated_client) -> None:  # noqa: ANN001
    client, _sf = isolated_client
    body = client.get("/api/dashboard").json()
    assert body["totalTrades"] == 0
    assert body["dayPnl"] == 0
    assert body["winRate"] == 0
    assert body["positions"] == []


def test_dashboard_snapshot_limits_reflect_real_guardrails_not_hardcoded_zero(isolated_client) -> None:  # noqa: ANN001
    """Regression: `dailyLossLimit`/`maxPositions`/`maxTradesPerDay` were
    left as literal `0` in the snapshot response after Tier 0 wiring,
    predating Tier 1's live guardrails landing — found live on 2026-07-30
    ("0% of ₹0 daily loss limit used" displayed on a real, non-mock
    dashboard, with real guardrails configured underneath it)."""
    client, _sf = isolated_client

    payload = {
        "capitalRupees": 20000,
        "maxDailyLossRupees": 700,
        "maxPositionSizePct": 25,
        "maxDrawdownPct": 20,
        "maxTradesPerDay": 10,
        "maxConcurrentPositions": 5,
        "riskPerTradePct": 1.5,
    }
    put_response = client.put("/api/engine/guardrails", json=payload)
    assert put_response.status_code == 200

    body = client.get("/api/dashboard/snapshot").json()
    assert body["dailyLossLimit"] == 700
    assert body["maxPositions"] == 5
    assert body["maxTradesPerDay"] == 10


def test_pnl_analysis_computes_real_summary(isolated_client) -> None:  # noqa: ANN001
    client, sf = isolated_client
    _seed_closed_trade(sf, net_pnl_paise=5_000)

    body = client.get("/api/pnl").json()
    assert body["totalTrades"] == 1
    assert body["winningTrades"] == 1
    assert body["totalPnl"] == 50.0
    assert body["sharpe"] is None  # n=1, far below MIN_TRADES_FOR_SHARPE


def test_pnl_analysis_respects_from_to_window(isolated_client) -> None:  # noqa: ANN001
    client, sf = isolated_client
    _seed_closed_trade(sf)

    outside = client.get(
        "/api/pnl", params={"from": "2020-01-01T00:00:00+00:00", "to": "2020-01-02T00:00:00+00:00"}
    ).json()
    assert outside["totalTrades"] == 0

    # Relative to `TODAY` (this module's docstring explains why: the router
    # queries a UTC window derived from an IST calendar date, so "today" must
    # be computed the same way the fixture data was seeded, never a literal
    # date string — which silently goes stale as soon as the real date moves
    # past whatever day it was hardcoded for).
    day_before = (TODAY - dt.timedelta(days=1)).isoformat()
    day_after = (TODAY + dt.timedelta(days=1)).isoformat()
    inside = client.get(
        "/api/pnl", params={"from": f"{day_before}T00:00:00+00:00", "to": f"{day_after}T00:00:00+00:00"}
    ).json()
    assert inside["totalTrades"] == 1


def test_trades_list_reflects_real_closed_trade(isolated_client) -> None:  # noqa: ANN001
    client, sf = isolated_client
    _seed_closed_trade(sf)

    body = client.get("/api/trades").json()
    assert len(body) == 1
    assert body[0]["symbol"] == "NIFTY30JUL2624500CE"
    assert body[0]["pnl"] == 50.0
    assert body[0]["orderId"] == "co-1"


def test_trades_list_filters_by_strategy_and_symbol(isolated_client) -> None:  # noqa: ANN001
    client, sf = isolated_client
    _seed_closed_trade(sf)

    assert client.get("/api/trades", params={"strategy": "nonexistent"}).json() == []
    assert len(client.get("/api/trades", params={"strategy": "orb"}).json()) == 1
    assert client.get("/api/trades", params={"symbol": "nonexistent"}).json() == []


def test_positions_list_reflects_real_open_position(isolated_client) -> None:  # noqa: ANN001
    client, sf = isolated_client
    _seed_open_position(sf)

    body = client.get("/api/positions").json()
    assert len(body) == 1
    assert body[0]["symbol"] == "BANKNIFTY30JUL2652000PE"
    assert body[0]["quantity"] == 60  # 2 lots x 30 lot_size
    assert body[0]["buyAvg"] == 80.0  # 8_000 paise -> Rs 80


def test_orders_list_reflects_real_folded_order(isolated_client) -> None:  # noqa: ANN001
    client, sf = isolated_client
    store = OrderEventStore(sf)
    store.append(
        OrderInitialized(client_order_id="co-3", symbol="NIFTY", exchange="NFO", side="BUY", quantity=65, ts=NOW)
    )
    store.append(OrderAccepted(client_order_id="co-3", venue_order_id="v-1", ts=NOW))
    store.append(
        OrderFilled(client_order_id="co-3", venue_trade_id="t-1", fill_qty=65, fill_price=Paise(10_000), ts=NOW)
    )

    body = client.get("/api/orders").json()
    assert len(body) == 1
    assert body[0]["id"] == "co-3"
    assert body[0]["status"] == "COMPLETE"
    assert body[0]["filledQty"] == 65
    assert body[0]["averagePrice"] == 100.0  # 10_000 paise -> Rs 100


def test_orders_list_only_includes_todays_orders(isolated_client) -> None:  # noqa: ANN001
    client, sf = isolated_client
    store = OrderEventStore(sf)
    yesterday = NOW - dt.timedelta(days=1)
    store.append(
        OrderInitialized(
            client_order_id="co-old", symbol="NIFTY", exchange="NFO", side="BUY", quantity=65, ts=yesterday
        )
    )

    assert client.get("/api/orders").json() == []


def test_cancel_order_honestly_reports_failure(isolated_client) -> None:  # noqa: ANN001
    client, _sf = isolated_client
    response = client.post("/api/orders/cancel", json={"id": "nonexistent"})
    assert response.status_code == 200
    assert response.json()["success"] is False


def test_execution_skipped_reflects_real_skipped_signals(isolated_client) -> None:  # noqa: ANN001
    """Regression, found live on 2026-07-31: `/api/execution/skipped`
    returned a literal `[]` stub even though real ORB signals had already
    fired and been rejected (e.g. by the cost-vs-edge sizing floor) — the
    router never called `recent_skipped_signals`."""
    client, sf = isolated_client
    with sf() as session:
        record_skipped_signal(
            session,
            ts=NOW,
            strategy="orb",
            instrument="NIFTY",
            reason="gross edge per lot (97500p) < round-trip cost (379471p) x min_edge_multiple (1.2)",
        )
        session.commit()

    body = client.get("/api/execution/skipped").json()
    assert len(body) == 1
    assert body[0]["symbol"] == "NIFTY"
    assert body[0]["strategy"] == "orb"
    assert "min_edge_multiple" in body[0]["reason"]


def test_daily_pnl_reflects_real_closed_trades(isolated_client) -> None:  # noqa: ANN001
    """Regression, found live on 2026-07-31 alongside `/api/execution/
    skipped`: `/api/daily-pnl` and `/api/equity-curve` were still permanent
    `[]` stubs even with real closed trades on record — a market-hours
    Performance page showed n=0/₹0.00 net to the right of a Dashboard
    showing real trades and a real loss."""
    client, sf = isolated_client
    _seed_closed_trade(sf, net_pnl_paise=5_000)

    body = client.get("/api/daily-pnl").json()
    assert len(body) == 1
    assert body[0]["date"] == TODAY.isoformat()
    assert body[0]["pnl"] == 50.0
    assert body[0]["trades"] == 1


def test_equity_curve_reflects_capital_plus_real_realized_pnl(isolated_client) -> None:  # noqa: ANN001
    client, sf = isolated_client
    _seed_closed_trade(sf, net_pnl_paise=5_000)

    put_response = client.put(
        "/api/engine/guardrails",
        json={
            "capitalRupees": 20000,
            "maxDailyLossRupees": 700,
            "maxPositionSizePct": 25,
            "maxDrawdownPct": 20,
            "maxTradesPerDay": 10,
            "maxConcurrentPositions": 5,
            "riskPerTradePct": 1.5,
        },
    )
    assert put_response.status_code == 200

    body = client.get("/api/equity-curve").json()
    assert len(body) == 1
    assert body[0]["date"] == TODAY.isoformat()
    assert body[0]["value"] == 20_050.0  # 20,000 capital + 50 realized
