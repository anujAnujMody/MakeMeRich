"""`te.strategy.orb.OrbStrategy` — re-derived from `engine/strategies/orbs/
orbs.py` (spec only) under the `Evaluation`/`ConditionResult` interface.

NOTE: every test appends all of its bars via a SINGLE `BarStore.append()`
call. `BarStore.append()` (Phase 1) names each part file from
`datetime.now()` at microsecond resolution with a per-call-local counter, so
two separate `append()` calls issued back-to-back in the same test can
collide on the same filename and silently overwrite each other — a
pre-existing Phase 1 issue, out of this phase's scope to fix, but real
enough to produce flaky ORB tests if not worked around here.
"""

from __future__ import annotations

import datetime as dt

import pandas as pd
import pytest

from te.data.barstore import BAR_COLUMNS, BarStore
from te.domain.clock import IST
from te.strategy.context import StrategyContext
from te.strategy.orb import OrbParams, OrbStrategy

INSTRUMENT = "NIFTY"
EXCHANGE = "NSE_INDEX"


def _bar(event_ts: dt.datetime, *, o: float, h: float, low: float, c: float, v: int) -> dict[str, object]:
    return {
        "symbol": INSTRUMENT,
        "exchange": EXCHANGE,
        "event_ts": event_ts,
        "interval": "1m",
        "o": o,
        "h": h,
        "l": low,
        "c": c,
        "v": v,
        "oi": 0,
        "ingested_at": event_ts,
        "source": "test",
    }


def _open(minute: int) -> dt.datetime:
    base = dt.datetime(2026, 7, 29, 9, 15, tzinfo=IST)
    return base + dt.timedelta(minutes=minute)


@pytest.fixture
def store(tmp_path) -> BarStore:  # noqa: ANN001
    return BarStore(tmp_path)


def _opening_range_rows(*, high: float = 105, low: float = 95) -> list[dict[str, object]]:
    return [
        _bar(_open(0), o=100, h=high, low=low, c=100, v=1_000),
        _bar(_open(1), o=100, h=101, low=99, c=100.5, v=1_000),
        _bar(_open(2), o=100, h=101, low=99, c=100.2, v=1_000),
    ]


def _write(store: BarStore, rows: list[dict[str, object]]) -> None:
    store.append(pd.DataFrame(rows, columns=list(BAR_COLUMNS)))


def test_skips_with_real_reason_when_opening_range_not_yet_formed(store: BarStore) -> None:
    _write(store, [_bar(_open(0), o=100, h=101, low=99, c=100, v=1_000)])

    strategy = OrbStrategy(OrbParams(opening_range_minutes=15, min_opening_bars=3))
    ctx = StrategyContext(store=store, instrument=INSTRUMENT, exchange=EXCHANGE, as_of=_open(1))

    evaluation = strategy.evaluate(ctx)

    assert evaluation.verdict == "skipped"
    assert "opening range not yet formed" in evaluation.reason
    assert strategy.last_signal is None
    data_cond = evaluation.conditions[0]
    assert data_cond.evaluated is True
    assert data_cond.actual == "1 bars"
    # short-circuited conditions carry the real "not reached" marker, never a
    # fabricated actual value.
    for later in evaluation.conditions[1:]:
        assert later.evaluated is False
        assert later.actual == "not reached"


def test_evaluation_rejects_condition_without_actual_orb_never_bypasses_it(store: BarStore) -> None:
    """Proves orb.py's short-circuit path goes through the real
    `ConditionResult` constructor rather than hand-building a dict — the
    constructor itself is what enforces evaluated=False -> actual=='not
    reached' (Phase 2's invariant)."""
    _write(store, [_bar(_open(0), o=100, h=101, low=99, c=100, v=1_000)])
    strategy = OrbStrategy(OrbParams(opening_range_minutes=15, min_opening_bars=3))
    ctx = StrategyContext(store=store, instrument=INSTRUMENT, exchange=EXCHANGE, as_of=_open(1))

    evaluation = strategy.evaluate(ctx)

    short_circuited = evaluation.conditions[1]
    assert short_circuited.evaluated is False
    assert short_circuited.actual == "not reached"
    with pytest.raises(ValueError, match="not reached"):
        from te.domain.evaluation import ConditionResult

        ConditionResult(label="x", required="y", actual="fabricated", passed=False, evaluated=False)


def test_skips_when_no_bar_after_opening_range_yet(store: BarStore) -> None:
    _write(store, _opening_range_rows())
    strategy = OrbStrategy(OrbParams(opening_range_minutes=15, min_opening_bars=3))
    ctx = StrategyContext(store=store, instrument=INSTRUMENT, exchange=EXCHANGE, as_of=_open(3))

    evaluation = strategy.evaluate(ctx)

    assert evaluation.verdict == "skipped"
    assert "no bar has closed after the opening range" in evaluation.reason
    assert strategy.last_signal is None


def test_skips_when_close_stays_inside_range(store: BarStore) -> None:
    rows = [*_opening_range_rows(high=105, low=95), _bar(_open(15), o=100, h=102, low=98, c=100, v=1_000)]
    _write(store, rows)
    strategy = OrbStrategy(OrbParams(opening_range_minutes=15, min_opening_bars=3))
    ctx = StrategyContext(store=store, instrument=INSTRUMENT, exchange=EXCHANGE, as_of=_open(16))

    evaluation = strategy.evaluate(ctx)

    assert evaluation.verdict == "skipped"
    assert "no breakout" in evaluation.reason
    assert strategy.last_signal is None
    breakout_cond = evaluation.conditions[1]
    assert breakout_cond.evaluated is True
    assert breakout_cond.passed is False
    assert "close=100.00" in breakout_cond.actual


def test_skips_on_breakout_without_volume_confirmation(store: BarStore) -> None:
    rows = [
        *_opening_range_rows(high=105, low=95),  # avg opening volume = 1000
        _bar(_open(15), o=100, h=110, low=100, c=108, v=500),  # breaks out, weak volume
    ]
    _write(store, rows)
    strategy = OrbStrategy(OrbParams(opening_range_minutes=15, min_opening_bars=3))
    ctx = StrategyContext(store=store, instrument=INSTRUMENT, exchange=EXCHANGE, as_of=_open(16))

    evaluation = strategy.evaluate(ctx)

    assert evaluation.verdict == "skipped"
    assert "volume" in evaluation.reason
    assert strategy.last_signal is None
    volume_cond = evaluation.conditions[-1]
    assert volume_cond.evaluated is True
    assert volume_cond.passed is False


def test_trades_long_call_on_confirmed_upside_breakout(store: BarStore) -> None:
    rows = [
        *_opening_range_rows(high=105, low=95),  # avg opening volume = 1000
        _bar(_open(15), o=100, h=110, low=100, c=108, v=1_500),
    ]
    _write(store, rows)
    strategy = OrbStrategy(OrbParams(opening_range_minutes=15, min_opening_bars=3))
    ctx = StrategyContext(store=store, instrument=INSTRUMENT, exchange=EXCHANGE, as_of=_open(16))

    evaluation = strategy.evaluate(ctx)

    assert evaluation.verdict == "traded"
    assert all(c.evaluated and c.passed for c in evaluation.conditions)
    assert strategy.last_signal is not None
    assert strategy.last_signal.direction == "long_call"
    assert strategy.last_signal.instrument == INSTRUMENT
    assert strategy.last_signal.entry_premium == 10_800  # 108.00 rupees -> paise


def test_does_not_resignal_while_price_merely_remains_beyond_the_range(store: BarStore) -> None:
    """Regression for the churn bug found live on 2026-07-31. ORB read only
    the LATEST bar and asked "is price outside the range?" — true for every
    minute a move lasts, so one 10:00 breakout that held until 10:30
    re-signalled ~30 times, each one opening a fresh position at full
    round-trip cost. A breakout is a CROSSING: the first bar to close beyond
    the range, per the standard "wait for a candle to close beyond the
    range" formulation. Bar 16 here is still beyond the range, but bar 15
    already crossed — so bar 16 must NOT re-signal."""
    rows = [
        *_opening_range_rows(high=105, low=95),
        _bar(_open(15), o=100, h=110, low=100, c=108, v=1_500),  # the crossing
        _bar(_open(16), o=108, h=112, low=107, c=111, v=1_500),  # still outside, not a new crossing
    ]
    _write(store, rows)
    strategy = OrbStrategy(OrbParams(opening_range_minutes=15, min_opening_bars=3))
    ctx = StrategyContext(store=store, instrument=INSTRUMENT, exchange=EXCHANGE, as_of=_open(17))

    evaluation = strategy.evaluate(ctx)

    assert evaluation.verdict == "skipped"
    assert strategy.last_signal is None
    assert "crossing" in evaluation.reason or "earlier bar" in evaluation.reason


def test_a_genuine_second_crossing_signals_again(store: BarStore) -> None:
    """The flip side: edge-triggering must not become a silent one-shot. If
    price breaks out, falls back INSIDE the range, then breaks out again,
    that later move is a real new crossing and should signal — this is what
    a blunt "one entry per day" rule would have wrongly suppressed."""
    rows = [
        *_opening_range_rows(high=105, low=95),
        _bar(_open(15), o=100, h=110, low=100, c=108, v=1_500),  # first crossing
        _bar(_open(16), o=108, h=109, low=99, c=100, v=1_200),  # back INSIDE the range
        _bar(_open(17), o=100, h=112, low=100, c=109, v=1_500),  # second, genuine crossing
    ]
    _write(store, rows)
    strategy = OrbStrategy(OrbParams(opening_range_minutes=15, min_opening_bars=3))
    ctx = StrategyContext(store=store, instrument=INSTRUMENT, exchange=EXCHANGE, as_of=_open(18))

    evaluation = strategy.evaluate(ctx)

    assert evaluation.verdict == "traded"
    assert strategy.last_signal is not None
    assert strategy.last_signal.direction == "long_call"


def test_trades_long_put_on_confirmed_downside_breakout(store: BarStore) -> None:
    rows = [*_opening_range_rows(high=105, low=95), _bar(_open(15), o=100, h=95, low=88, c=90, v=2_000)]
    _write(store, rows)
    strategy = OrbStrategy(OrbParams(opening_range_minutes=15, min_opening_bars=3))
    ctx = StrategyContext(store=store, instrument=INSTRUMENT, exchange=EXCHANGE, as_of=_open(16))

    evaluation = strategy.evaluate(ctx)

    assert evaluation.verdict == "traded"
    assert strategy.last_signal is not None
    assert strategy.last_signal.direction == "long_put"


def test_last_signal_resets_between_evaluate_calls(store: BarStore, tmp_path) -> None:  # noqa: ANN001
    rows = [
        *_opening_range_rows(high=105, low=95),
        _bar(_open(15), o=100, h=110, low=100, c=108, v=1_500),
    ]
    _write(store, rows)
    strategy = OrbStrategy(OrbParams(opening_range_minutes=15, min_opening_bars=3))

    ctx_trade = StrategyContext(store=store, instrument=INSTRUMENT, exchange=EXCHANGE, as_of=_open(16))
    strategy.evaluate(ctx_trade)
    assert strategy.last_signal is not None

    # A second, independent evaluate() with insufficient data must not leak
    # the previous cycle's signal forward.
    empty_store = BarStore(tmp_path / "empty")
    ctx_skip = StrategyContext(store=empty_store, instrument=INSTRUMENT, exchange=EXCHANGE, as_of=_open(1))
    evaluation = strategy.evaluate(ctx_skip)
    assert evaluation.verdict == "skipped"
    assert strategy.last_signal is None
