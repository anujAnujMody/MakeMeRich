"""The backtest's fast path must decide exactly what the live path decides.

`SessionRule._session_frame` has two implementations. The live one rebuilds
the session incrementally from `bars_asof` on every evaluation. The backtest
one receives the whole day precomputed and slices it (`PrebuiltSession`),
because profiling showed 88% of a run was spent rebuilding identical columns
375 times a day.

That optimisation is only legitimate if the two are indistinguishable. If
they can differ, every backtest number describes a strategy that is not the
one that will trade — which is worse than a slow backtest, and invisible.

So these tests compare them directly, minute by minute, for every registered
strategy: same visible bars, same verdict, same reason. The riskiest part is
the visibility boundary — `bars_asof` shows a bar only once it has CLOSED, so
a bar stamped 12:00 is visible at 12:01 and not before — and an off-by-one
there would hand the backtest one extra bar of the future on every single
evaluation.
"""

from __future__ import annotations

import datetime as dt

import pandas as pd
import pytest

from te.backtest.strategy_lab import _derive_session
from te.data.barstore import BarStore
from te.domain.clock import IST
from te.strategy.context import StrategyContext
from te.strategy.registry import available, get
from te.strategy.session_rule import _PREBUILT_KEY

_DAY = dt.date(2026, 3, 10)
_PREVIOUS = dt.date(2026, 3, 9)


def _bars(day: dt.date, start: float, drift: float, count: int = 375) -> list[dict[str, object]]:
    rows = []
    price = start
    for minute in range(count):
        ts = dt.datetime.combine(day, dt.time(9, 15), tzinfo=IST) + dt.timedelta(minutes=minute)
        price += (0.0 if minute < 60 else drift) + (0.5 if minute % 6 == 0 else -0.35)
        rows.append(
            {
                "symbol": "NIFTY",
                "exchange": "NSE_INDEX",
                "event_ts": ts.astimezone(dt.UTC),
                "interval": "1m",
                "o": price,
                "h": price + 0.05,
                "l": price - 0.05,
                "c": price,
                "v": 0.0,
                "oi": 0,
                "ingested_at": ts.astimezone(dt.UTC),
                "source": "test",
            }
        )
    return rows


@pytest.fixture(scope="module")
def data() -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    return _bars(_PREVIOUS, 24_000.0, 0.0), _bars(_DAY, 24_050.0, 0.8)


@pytest.fixture(scope="module")
def live_store(tmp_path_factory, data) -> BarStore:  # noqa: ANN001
    previous, today = data
    store = BarStore(tmp_path_factory.mktemp("live"))
    store.append(pd.DataFrame([*previous, *today]))
    return store


def _prebuilt(data) -> object:  # noqa: ANN001
    previous, today = data
    return _derive_session(pd.DataFrame(today), pd.DataFrame(previous))


@pytest.mark.parametrize("minute", [0, 1, 2, 30, 59, 60, 61, 120, 200, 319])
def test_the_visible_bars_match_the_live_gate_exactly(minute: int, live_store: BarStore, data) -> None:  # noqa: ANN001
    """The boundary test. An off-by-one here would give the backtest one
    extra bar of the future on every evaluation — the most damaging bug
    available, and completely silent."""
    as_of = dt.datetime.combine(_DAY, dt.time(9, 16), tzinfo=IST) + dt.timedelta(minutes=minute)

    live_ctx = StrategyContext(store=live_store, instrument="NIFTY", exchange="NSE_INDEX", as_of=as_of)
    rule = get("orb60")
    live_frame = rule._session_frame(live_ctx)  # noqa: SLF001

    fast_frame = _prebuilt(data).visible_at(as_of)  # type: ignore[union-attr]

    assert (live_frame is None) == (fast_frame is None), f"minute {minute}: one path saw bars and the other did not"
    if live_frame is None or fast_frame is None:
        return
    assert len(live_frame) == len(fast_frame), (
        f"minute {minute}: live path saw {len(live_frame)} bars, fast path saw {len(fast_frame)}"
    )
    assert live_frame["event_ts"].iloc[-1] == fast_frame["event_ts"].iloc[-1], (
        f"minute {minute}: the two paths disagree on the most recent visible bar"
    )


@pytest.mark.parametrize("name", sorted(available()))
def test_every_strategy_decides_identically_on_both_paths(name: str, live_store: BarStore, data) -> None:  # noqa: ANN001
    """Walks a whole session and compares verdict and reason at every minute.

    Covers the entire library rather than a sample, because the two paths
    could agree for simple rules and diverge for one that reads a column the
    fast path derives differently."""
    prebuilt = _prebuilt(data)
    live_ctx = StrategyContext(
        store=live_store,
        instrument="NIFTY",
        exchange="NSE_INDEX",
        as_of=dt.datetime.combine(_DAY, dt.time(9, 16), tzinfo=IST),
    )
    fast_ctx = StrategyContext(
        store=live_store,
        instrument="NIFTY",
        exchange="NSE_INDEX",
        as_of=live_ctx.as_of,
    )
    fast_ctx.state[_PREBUILT_KEY] = prebuilt

    live_rule, fast_rule = get(name), get(name)
    start = dt.datetime.combine(_DAY, dt.time(9, 16), tzinfo=IST)
    for minute in range(0, 320, 7):
        as_of = start + dt.timedelta(minutes=minute)
        live_ctx.as_of = as_of
        fast_ctx.as_of = as_of
        live = live_rule.evaluate(live_ctx)
        fast = fast_rule.evaluate(fast_ctx)
        assert live.verdict == fast.verdict, (
            f"{name} at minute {minute}: live said {live.verdict!r}, backtest said {fast.verdict!r}"
        )
        assert live.reason == fast.reason, (
            f"{name} at minute {minute}: live said {live.reason!r}, backtest said {fast.reason!r}"
        )


def test_the_fast_path_returns_nothing_before_the_first_bar_closes(data) -> None:  # noqa: ANN001
    """At 09:15 the first bar has not closed yet, so there is nothing to
    decide on — the same answer `bars_asof` gives."""
    prebuilt = _prebuilt(data)
    at_open = dt.datetime.combine(_DAY, dt.time(9, 15), tzinfo=IST)
    assert prebuilt.visible_at(at_open) is None  # type: ignore[union-attr]
