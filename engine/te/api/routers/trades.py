import datetime as dt

from fastapi import APIRouter, Query, Response

from te.api.db import session_factory
from te.api.provenance import set_provenance
from te.api.schemas.trading import Trade
from te.domain.clock import assume_utc
from te.domain.money import Paise, rupees
from te.persistence.repos.paper_trading import recent_trades

router = APIRouter(prefix="/api/trades", tags=["trades"])


@router.get("", response_model=list[Trade])
def list_trades(
    response: Response,
    from_: str | None = Query(default=None, alias="from"),
    to: str | None = None,
    strategy: str | None = None,
    symbol: str | None = None,
) -> list[Trade]:
    """Closed trades, newest first, from the real `trades` table (Phase 4's
    execution core) — filtered in Python by `from`/`to`/`strategy`/`symbol`
    (no dedicated indexed query for this filter combination yet; the volume
    at this stage of the project doesn't warrant one).

    `Trade.pnl` is a single bare field on the frozen dashboard contract
    (`dashboard/src/types/index.ts`) — populated with the NET P&L (never
    gross), matching the plan's "profit always means net of costs" rule even
    though the contract's field name doesn't say so."""
    with session_factory() as session:
        rows = recent_trades(session, limit=500)

    # See te/api/routers/pnl.py's identical comment: SQLite hands
    # `closed_at` back tz-NAIVE regardless of the column's `timezone=True`
    # flag, so `assume_utc()` reattaches UTC before comparing.
    from_dt = assume_utc(dt.datetime.fromisoformat(from_)) if from_ else None
    to_dt = assume_utc(dt.datetime.fromisoformat(to)) if to else None
    if from_dt is not None:
        rows = [r for r in rows if assume_utc(r.closed_at) >= from_dt]
    if to_dt is not None:
        rows = [r for r in rows if assume_utc(r.closed_at) <= to_dt]
    if strategy is not None:
        rows = [r for r in rows if r.strategy == strategy]
    if symbol is not None:
        rows = [r for r in rows if r.symbol == symbol]

    set_provenance(response, provenance="paper" if rows else "none", sample_size=len(rows))
    return [
        Trade(
            id=str(row.id),
            symbol=row.symbol,
            exchange=row.exchange,
            # Every position here is a BUY (directional option buying only —
            # see te.domain.signal.Direction); the close leg is always a SELL.
            transactionType="SELL",
            quantity=row.lots * row.lot_size,
            price=float(rupees(Paise(row.exit_premium_paise))),
            timestamp=row.closed_at.isoformat(),
            strategy=row.strategy,
            pnl=float(rupees(Paise(row.net_pnl_paise))),
            orderId=row.client_order_id,
        )
        for row in rows
    ]
