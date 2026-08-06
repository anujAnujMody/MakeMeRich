"""Mirrors the PnLAnalysis slice of dashboard/src/types/index.ts.

`period` is typed as a plain `dict[str, str]` with keys `"from"`/`"to"`
rather than a nested Pydantic model, since `from` is a reserved word in
Python and this sidesteps needing an alias just to hold two string keys.
"""

from pydantic import BaseModel


class PnLAnalysis(BaseModel):
    totalPnl: float
    winRate: float
    totalTrades: int
    winningTrades: int
    losingTrades: int
    avgWin: float
    avgLoss: float
    maxDrawdown: float
    # Banned by the plan when n<100: omit rather than fabricate.
    sharpe: float | None = None
    period: dict[str, str]
