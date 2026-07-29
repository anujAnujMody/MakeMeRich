"""`SimulatedBroker` — the paper venue. Implements `BrokerPort` structurally
(no inheritance needed). Per the plan, this is the ONE fill path shared by
paper trading (this phase's only wired venue) and, later, `te/backtest/
fills.py` — so paper P&L is net of costs from day one and never drifts from
a second calculation.

**Fill model** (deliberately simple, documented, later phases may refine):
every order fills IMMEDIATELY and IN FULL at `intent.limit_price`, adjusted
by a small deterministic slippage (`slippage_bps`, default 0) applied in the
direction that is always worse for the trader — price up for a BUY, price
down for a SELL. `intent.limit_price` is required for both `LIMIT` and
`MARKET` orders in this simulated venue (no live market data is wired to the
broker layer yet — a `MARKET` order here means "fill at the caller-supplied
reference price", not "fill at whatever the live tape says"; wiring a real
price feed is later-phase work).

Every fill is run through `te.domain.costs.CostModel.leg()` before being
reported, so `FillReport.net_amount_paise` is always net — the plan's
structural guarantee that paper P&L can never accidentally be gross.
"""

from __future__ import annotations

import datetime as dt
import uuid
from decimal import ROUND_HALF_UP, Decimal

from te.broker.protocol import FillReport, OrderStatusReport, PlaceOrderAck, PositionReport
from te.domain.costs import CostModel
from te.domain.money import Paise
from te.domain.orders import OrderIntent

_BPS_DIVISOR = Decimal(10_000)


def _round_paise(value: Decimal) -> Paise:
    return Paise(int(value.quantize(Decimal(1), rounding=ROUND_HALF_UP)))


class SimulatedBroker:
    """Paper venue. Satisfies `te.broker.protocol.BrokerPort` structurally."""

    def __init__(
        self,
        *,
        cost_model: CostModel,
        on: dt.date,
        slippage_bps: Decimal = Decimal(0),
    ) -> None:
        self._cost_model = cost_model
        self._on = on
        self._slippage_bps = slippage_bps
        self._orders: dict[str, OrderStatusReport] = {}
        self._fills: list[FillReport] = []
        self._positions: dict[tuple[str, str], int] = {}

    def place_order(self, intent: OrderIntent) -> PlaceOrderAck:
        if intent.limit_price is None:
            raise ValueError(
                "SimulatedBroker requires intent.limit_price (used as the fill/reference price for "
                "both LIMIT and MARKET orders — no live price feed is wired to the broker layer yet)"
            )
        venue_order_id = f"sim-{uuid.uuid4().hex[:12]}"
        ts = intent.ts
        fill_price = self._apply_slippage(intent)

        ack = PlaceOrderAck(client_order_id=intent.client_order_id, venue_order_id=venue_order_id, ts=ts)

        self._orders[intent.client_order_id] = OrderStatusReport(
            client_order_id=intent.client_order_id,
            venue_order_id=venue_order_id,
            symbol=intent.symbol,
            exchange=intent.exchange,
            side=intent.side,
            quantity=intent.quantity,
            filled_qty=intent.quantity,
            status="complete",
            ts=ts,
            average_price=fill_price,
        )

        costs = self._cost_model.leg(
            side=intent.side, premium=fill_price, qty=intent.quantity, exchange=intent.exchange, on=self._on
        )
        notional = Paise(fill_price * intent.quantity)
        gross_amount = Paise(-notional) if intent.side == "BUY" else notional
        net_amount = Paise(gross_amount - costs.total)

        fill = FillReport(
            client_order_id=intent.client_order_id,
            venue_order_id=venue_order_id,
            venue_trade_id=f"sim-trade-{uuid.uuid4().hex[:12]}",
            symbol=intent.symbol,
            exchange=intent.exchange,
            side=intent.side,
            fill_qty=intent.quantity,
            fill_price=fill_price,
            gross_amount_paise=gross_amount,
            cost_breakdown=costs,
            net_amount_paise=net_amount,
            ts=ts,
        )
        self._fills.append(fill)

        signed_qty = intent.quantity if intent.side == "BUY" else -intent.quantity
        key = (intent.symbol, intent.exchange)
        self._positions[key] = self._positions.get(key, 0) + signed_qty

        return ack

    def cancel_order(self, client_order_id: str, venue_order_id: str) -> None:
        # Fills happen synchronously in this simple model, so there is never
        # anything resting to cancel — a no-op is honest here, not a stub.
        report = self._orders.get(client_order_id)
        if report is None:
            raise KeyError(f"unknown client_order_id: {client_order_id!r}")

    def query_order(self, client_order_id: str) -> OrderStatusReport | None:
        return self._orders.get(client_order_id)

    def order_reports(self) -> list[OrderStatusReport]:
        return list(self._orders.values())

    def fill_reports(self) -> list[FillReport]:
        return list(self._fills)

    def position_reports(self) -> list[PositionReport]:
        return [
            PositionReport(symbol=symbol, exchange=exchange, quantity=qty)
            for (symbol, exchange), qty in self._positions.items()
        ]

    def _apply_slippage(self, intent: OrderIntent) -> Paise:
        assert intent.limit_price is not None
        if self._slippage_bps == 0:
            return intent.limit_price
        adjustment = _round_paise(Decimal(intent.limit_price) * self._slippage_bps / _BPS_DIVISOR)
        if intent.side == "BUY":
            return Paise(intent.limit_price + adjustment)
        return Paise(intent.limit_price - adjustment)
