"""`te.strategy.context.StrategyContext` — bar access MUST go through
`te.data.asof.bars_asof()`, never a raw `BarStore.read()`."""

from __future__ import annotations

import datetime as dt

import pandas as pd
import pytest

from te.data.barstore import BAR_COLUMNS, BarStore
from te.strategy.context import StrategyContext

UTC = dt.UTC


def _bar(event_ts: dt.datetime, *, o: float, h: float, l: float, c: float, v: int) -> dict[str, object]:
    return {
        "symbol": "NIFTY",
        "exchange": "NSE_INDEX",
        "event_ts": event_ts,
        "interval": "1m",
        "o": o,
        "h": h,
        "l": l,
        "c": c,
        "v": v,
        "oi": 0,
        "ingested_at": event_ts,
        "source": "test",
    }


@pytest.fixture
def populated_store(tmp_path) -> BarStore:  # noqa: ANN001
    store = BarStore(tmp_path)
    base = dt.datetime(2026, 7, 29, 9, 15, tzinfo=UTC)
    rows = [
        _bar(base + dt.timedelta(minutes=i), o=100 + i, h=101 + i, l=99 + i, c=100.5 + i, v=1_000)
        for i in range(5)
    ]
    store.append(pd.DataFrame(rows, columns=list(BAR_COLUMNS)))
    return store


def test_context_bars_only_returns_closed_bars(populated_store: BarStore) -> None:
    # as_of sits inside bar index 4's still-open interval (it opened at
    # 09:19 and closes at 09:20) — that bar must not be visible.
    as_of = dt.datetime(2026, 7, 29, 9, 19, 30, tzinfo=UTC)
    ctx = StrategyContext(
        store=populated_store, instrument="NIFTY", exchange="NSE_INDEX", as_of=as_of, interval="1m"
    )
    bars = ctx.bars(lookback=dt.timedelta(minutes=10))
    assert len(bars) == 4  # bars 0-3 closed by 09:19:30; bar 4 has not closed yet


def test_context_never_reads_a_raw_bar(populated_store: BarStore) -> None:
    """`StrategyContext.bars()` must delegate to `bars_asof`, not
    `BarStore.read` directly — proven here by checking a bar ingested AFTER
    as_of is excluded, which only `bars_asof`'s ingested_at filter does."""
    late = dt.datetime(2026, 7, 29, 9, 10, tzinfo=UTC)
    late_row = _bar(late, o=1, h=1, l=1, c=1, v=1)
    late_row["ingested_at"] = dt.datetime(2026, 7, 29, 12, 0, tzinfo=UTC)  # ingested well after as_of
    populated_store.append(pd.DataFrame([late_row], columns=list(BAR_COLUMNS)))

    as_of = dt.datetime(2026, 7, 29, 9, 30, tzinfo=UTC)
    ctx = StrategyContext(
        store=populated_store, instrument="NIFTY", exchange="NSE_INDEX", as_of=as_of, interval="1m"
    )
    bars = ctx.bars(lookback=dt.timedelta(minutes=30))
    assert (bars["o"] == 1).sum() == 0
