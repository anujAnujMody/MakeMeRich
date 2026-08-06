"""`StrategyContext` — the only way a `Strategy.evaluate()` implementation
may read bars. Wraps `te.data.asof.bars_asof()` (the point-in-time gate) so a
strategy can never accidentally call `BarStore.read()` directly and leak
future bars into a decision.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

import pandas as pd

from te.data.asof import bars_asof
from te.data.barstore import BarStore


@dataclass
class StrategyContext:
    """Everything one `Strategy.evaluate()` call needs: which bar store to
    read from, which instrument/exchange is being evaluated, the current
    (as-of) clock time, and a small mutable `state` dict a strategy may use
    to carry running state across cycles (e.g. a cached opening range)."""

    store: BarStore
    instrument: str
    exchange: str
    as_of: dt.datetime
    interval: str = "1m"
    state: dict[str, object] = field(default_factory=dict)
    #: Real expiry dates for `instrument`, when the caller can supply them —
    #: from the broker's listed chain live, from the contract archive in a
    #: backtest. `None` means "no calendar available", which a calendar-gated
    #: rule must treat as a reason to stand down rather than to guess.
    #:
    #: It lives on the context rather than as a derived frame column so both
    #: paths get it the same way; the previous backtest-only column meant a
    #: rule silently changed behaviour depending on who built its frame.
    expiry_dates: frozenset[dt.date] | None = None

    def bars(self, lookback: dt.timedelta) -> pd.DataFrame:
        """Point-in-time bar read for `self.instrument`, delegating entirely
        to `bars_asof` — never a raw `BarStore.read()`."""
        return bars_asof(self.store, self.instrument, self.as_of, lookback, interval=self.interval)
