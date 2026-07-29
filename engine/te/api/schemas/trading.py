"""Mirrors the Order/Trade/Position/MarketData slice of dashboard/src/types/index.ts."""

from typing import Literal

from pydantic import BaseModel, Field

OrderStatus = Literal["OPEN", "PENDING", "COMPLETE", "CANCELLED", "REJECTED"]
OrderType = Literal["MARKET", "LIMIT", "SL", "SL-M"]
ProductType = Literal["MIS", "NRML", "CNC"]
TransactionType = Literal["BUY", "SELL"]


class Position(BaseModel):
    symbol: str
    exchange: str
    quantity: float
    buyAvg: float
    sellAvg: float
    netQty: float
    netAvg: float
    m2m: float
    unrealisedPnl: float
    realisedPnl: float
    ltp: float


class Order(BaseModel):
    id: str
    symbol: str
    exchange: str
    transactionType: TransactionType
    quantity: float
    price: float
    triggerPrice: float
    status: OrderStatus
    orderType: OrderType
    productType: ProductType
    filledQty: float
    averagePrice: float
    createdAt: str
    updatedAt: str
    strategy: str | None = None


class PlaceOrderPayload(BaseModel):
    """Numeric bounds are load-bearing on an ORDER-PLACEMENT payload: without
    them `0`, negative quantities and NaN all validate. Pydantic's `gt`/`ge`
    constraints also reject NaN, since no comparison against NaN succeeds."""

    symbol: str
    exchange: str
    transactionType: TransactionType
    quantity: float = Field(gt=0)
    price: float = Field(ge=0)  # 0 is legitimate for a MARKET order
    triggerPrice: float | None = Field(default=None, ge=0)
    orderType: OrderType
    productType: ProductType


class CancelOrderPayload(BaseModel):
    id: str


class SuccessResponse(BaseModel):
    """The declared response shape for endpoints that report only whether the
    action was accepted, so they appear in `/docs` like every other handler
    instead of as an untyped bare `dict`."""

    success: bool


class SquareOffPayload(BaseModel):
    symbol: str
    exchange: str


class Trade(BaseModel):
    id: str
    symbol: str
    exchange: str
    transactionType: TransactionType
    quantity: float
    price: float
    timestamp: str
    strategy: str
    pnl: float
    orderId: str


class MarketData(BaseModel):
    symbol: str
    exchange: str
    ltp: float
    open: float
    high: float
    low: float
    close: float
    volume: float
    change: float
    changePercent: float
    timestamp: str


class RejectedOrder(Order):
    reason: str
