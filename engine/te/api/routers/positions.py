import datetime as dt

from fastapi import APIRouter, Response

from te.api.db import bar_store, charge_rate_table, session_factory, settings
from te.api.provenance import set_provenance
from te.api.schemas.trading import Position, SquareOffPayload, SuccessResponse
from te.data.asof import bars_asof, latest_close_paise
from te.domain.clock import IST
from te.domain.costs import CostModel, select_rates
from te.domain.money import Paise, rupees
from te.domain.pnl import mark_to_market_pnl
from te.engine.cycle import square_off_position as _square_off_position
from te.execution.manager import build_paper_execution_stack
from te.persistence.models import OpenPositionRow
from te.persistence.repos.paper_trading import find_open_position, open_positions

router = APIRouter(prefix="/api/positions", tags=["positions"])


def position_from_row(
    row: OpenPositionRow, *, current_premium: Paise | None = None, on: dt.date | None = None
) -> Position:
    """Shared with `dashboard.py`'s `DashboardData.positions`, so the two
    endpoints can never quietly disagree on how an `OpenPositionRow` maps to
    the dashboard contract's `Position` shape.

    `current_premium` is the caller's mark (via `te.data.asof.
    latest_close_paise`); when supplied, `ltp`/`m2m`/`unrealisedPnl` are
    real, via the same `mark_to_market_pnl` formula a real close uses — when
    omitted, they stay honestly `0` rather than fabricated.

    Directional option BUYING only (`te.domain.signal.Direction` is
    `long_call`/`long_put`, never short), so `buyAvg` is always populated
    and `sellAvg` stays 0 rather than a meaningless guess."""
    entry = Paise(row.entry_premium_paise)
    qty = row.lots * row.lot_size
    if current_premium is not None:
        on = on or dt.datetime.now(IST).date()
        ltp = float(rupees(current_premium))
        m2m = float(rupees(Paise((current_premium - entry) * qty)))
        unrealised = float(
            rupees(
                Paise(
                    mark_to_market_pnl(
                        entry_premium=entry,
                        current_premium=current_premium,
                        qty=qty,
                        exchange=row.exchange,
                        cost_model=CostModel(select_rates(charge_rate_table, on)),
                        on=on,
                    )
                )
            )
        )
    else:
        ltp = 0.0
        m2m = 0.0
        unrealised = 0.0
    return Position(
        symbol=row.symbol,
        exchange=row.exchange,
        quantity=qty,
        buyAvg=float(rupees(entry)),
        sellAvg=0.0,
        netQty=qty,
        netAvg=float(rupees(entry)),
        m2m=m2m,
        unrealisedPnl=unrealised,
        realisedPnl=0.0,
        ltp=ltp,
    )


@router.get("", response_model=list[Position])
def list_positions(response: Response) -> list[Position]:
    """Open paper positions, read from the real `open_positions` table (Phase
    4's execution core) — the execution core landed in Phase 3/4; this router
    was simply never repointed at it afterwards. Marked to market against
    the latest recorded bar for each position's symbol."""
    as_of = dt.datetime.now(IST)
    with session_factory() as session:
        rows = open_positions(session)
    set_provenance(response, provenance="paper" if rows else "none", sample_size=len(rows))
    return [
        position_from_row(
            row,
            current_premium=Paise(latest_close_paise(bar_store, row.symbol, as_of, fallback=row.entry_premium_paise)),
            on=as_of.date(),
        )
        for row in rows
    ]


@router.post("/squareoff", response_model=SuccessResponse)
def square_off_position(payload: SquareOffPayload, response: Response) -> SuccessResponse:
    """Squares off one open paper position immediately, via the same
    `ExecutionManager`/`SimulatedBroker` route an automatic exit uses (see
    `te.engine.cycle.square_off_position`) — real, not a display flip.
    Honestly reports `success=False` (never a fabricated success) when
    there's no matching open position, or no recent bar to mark the close
    at — closing at a stale/invented price would misreport a P&L that
    isn't real."""
    as_of = dt.datetime.now(IST)
    with session_factory() as session:
        row = find_open_position(session, symbol=payload.symbol, exchange=payload.exchange)
    if row is None:
        set_provenance(response, not_ready_reason=f"no open position for {payload.symbol} on {payload.exchange}")
        return SuccessResponse(success=False)

    bars = bars_asof(bar_store, payload.symbol, as_of, lookback=dt.timedelta(minutes=5))
    if bars.empty:
        set_provenance(response, not_ready_reason="no recent price data available to close this position")
        return SuccessResponse(success=False)
    current_premium = Paise(latest_close_paise(bar_store, payload.symbol, as_of, fallback=row.entry_premium_paise))

    cost_model, execution = build_paper_execution_stack(
        session_factory,
        charge_rate_table=charge_rate_table,
        max_orders_per_second=settings.max_orders_per_second,
        on=as_of.date(),
    )

    closed_id = _square_off_position(
        session_factory=session_factory,
        execution=execution,
        cost_model=cost_model,
        symbol=payload.symbol,
        exchange=payload.exchange,
        current_premium=current_premium,
        as_of=as_of,
    )
    set_provenance(response, provenance="paper", sample_size=1)
    return SuccessResponse(success=closed_id is not None)
