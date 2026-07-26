from pathlib import Path
from typing import Any

from fastapi import FastAPI
from pydantic import BaseModel

from engine.db import get_trades, init_db
from engine.self_learning import ParamOptimizer, TradeAnalyzer

app = FastAPI(title="Trading Engine API")


def get_trades_from_db(db_path: str = "data/trades.db") -> list[Any]:
    p = Path(db_path)
    if not p.exists():
        return []
    conn = init_db(str(p))
    try:
        return get_trades(conn, limit=10000)
    finally:
        conn.close()


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/learning/stats")
def learning_stats(
    strategy: str | None = None,
    symbol: str | None = None,
) -> dict[str, Any]:
    trades = get_trades_from_db()
    if strategy:
        trades = [t for t in trades if t.strategy == strategy]
    if symbol:
        trades = [t for t in trades if t.symbol == symbol]

    analyzer = TradeAnalyzer(trades)
    return {
        "overall": analyzer.overall(),
        "by_strategy": analyzer.by_strategy(),
        "by_hour": analyzer.by_hour(),
        "by_day": analyzer.by_day(),
    }


class OptimizeRequest(BaseModel):
    strategy: str
    param_grid: dict[str, list[float]]


@app.post("/api/learning/optimize")
def param_optimize(req: OptimizeRequest) -> dict[str, Any]:
    trades = get_trades_from_db()
    optimizer = ParamOptimizer(trades)
    results = optimizer.grid_search(req.strategy, req.param_grid)
    return {"results": results}
