import datetime as dt

from fastapi import APIRouter, Query, Response

from te.api.db import session_factory
from te.api.provenance import set_provenance
from te.api.schemas.pnl import PnLAnalysis
from te.api.trade_stats import MIN_TRADES_FOR_SHARPE, sample_sharpe, summarize_trades
from te.domain.clock import assume_utc
from te.persistence.repos.paper_trading import recent_trades

router = APIRouter(prefix="/api/pnl", tags=["pnl"])


@router.get("", response_model=PnLAnalysis)
def get_pnl_analysis(
    response: Response,
    from_: str | None = Query(default=None, alias="from"),
    to: str | None = None,
) -> PnLAnalysis:
    """P&L breakdown over the `from`..`to` window, from the real `trades`
    table. `sharpe` stays `None` below `MIN_TRADES_FOR_SHARPE` closed trades
    — a Sharpe with no sample is exactly the fabricated-metric class this API
    refuses to emit, unchanged from the Phase-0 stub this replaces."""
    with session_factory() as session:
        rows = recent_trades(session, limit=500)

    # `TradeRow.closed_at` comes back from SQLite as a NAIVE datetime
    # regardless of the column's `timezone=True` flag (a SQLite+SQLAlchemy
    # quirk) — every value was written already normalised to UTC, so
    # `assume_utc()` is the sanctioned way to reattach UTC tzinfo before
    # comparing. Applied to the query params too (lenient, not `to_utc()`'s
    # strict-reject-naive) since a `from`/`to` string with no offset is a
    # plausible caller mistake, not something worth a raw 500 for.
    from_dt = assume_utc(dt.datetime.fromisoformat(from_)) if from_ else None
    to_dt = assume_utc(dt.datetime.fromisoformat(to)) if to else None
    if from_dt is not None:
        rows = [r for r in rows if assume_utc(r.closed_at) >= from_dt]
    if to_dt is not None:
        rows = [r for r in rows if assume_utc(r.closed_at) <= to_dt]

    summary = summarize_trades(rows)
    set_provenance(response, provenance="paper" if rows else "none", sample_size=len(rows))
    return PnLAnalysis(
        totalPnl=summary.total_pnl,
        winRate=summary.win_rate,
        totalTrades=summary.total_trades,
        winningTrades=summary.winning_trades,
        losingTrades=summary.losing_trades,
        avgWin=summary.avg_win,
        avgLoss=summary.avg_loss,
        maxDrawdown=summary.max_drawdown,
        sharpe=(
            sample_sharpe([r.net_pnl_paise for r in rows])
            if summary.total_trades >= MIN_TRADES_FOR_SHARPE
            else None
        ),
        period={"from": from_ or "", "to": to or ""},
    )
