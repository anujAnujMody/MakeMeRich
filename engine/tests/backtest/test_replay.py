"""ORB history replay — `te.backtest.replay` and `te.data.history_backfill`.

The load-bearing property is that replay is not a second implementation of
the strategy: it walks a clock and calls the live `OrbStrategy` through the
live `StrategyContext` and the live `bars_asof` gate. So the tests that
matter here are about the things replay itself owns — point-in-time honesty,
timestamp parsing, and idempotency — not about ORB's own logic, which has
its own suite.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pandas as pd
import pytest

from te.backtest.replay import _DayCacheStore, replay_orb
from te.broker.openalgo_rest import HistoryBar
from te.data.barstore import BAR_COLUMNS, BarStore
from te.data.history_backfill import SOURCE, _parse_ts, backfill_index_bars, month_windows
from te.domain.clock import IST
from te.persistence.db import make_engine, make_session_factory
from te.persistence.models import Base, CycleEvaluationRow

SYMBOL = "NIFTY"
EXCHANGE = "NSE_INDEX"


class _OneBarClient:
    """Returns a single epoch-stamped bar, whatever is asked of it."""

    def history(self, *a: object, **k: object) -> list[HistoryBar]:
        return [HistoryBar(timestamp="1780285500", open=1.0, high=2.0, low=0.5, close=1.5, volume=0.0, oi=0.0)]


def _open(day: dt.date, minute: int) -> dt.datetime:
    return dt.datetime.combine(day, dt.time(9, 15), tzinfo=IST) + dt.timedelta(minutes=minute)


def _bar(ts: dt.datetime, *, o: float, h: float, low: float, c: float) -> dict[str, object]:
    return {
        "symbol": SYMBOL, "exchange": EXCHANGE, "event_ts": ts, "interval": "1m",
        "o": o, "h": h, "l": low, "c": c, "v": 0.0, "oi": 0.0,
        "ingested_at": ts + dt.timedelta(minutes=1), "source": SOURCE,
    }


@pytest.fixture
def session_factory(tmp_path: Path):  # noqa: ANN201
    engine = make_engine(f"sqlite:///{tmp_path / 'replay.db'}")
    Base.metadata.create_all(engine)
    return make_session_factory(engine)


@pytest.fixture
def breakout_store(tmp_path: Path) -> BarStore:
    """A single day: a flat 15-minute opening range, then one clean upside
    crossing — the minimal shape that must produce exactly one firing."""
    store = BarStore(tmp_path / "bars")
    day = dt.date(2026, 6, 2)
    rows = [_bar(_open(day, m), o=100, h=101, low=99, c=100) for m in range(60)]
    rows.append(_bar(_open(day, 60), o=100, h=105, low=100, c=104))  # crosses out
    rows.append(_bar(_open(day, 61), o=104, h=106, low=103, c=105))  # stays out
    store.append(pd.DataFrame(rows, columns=list(BAR_COLUMNS)))
    return store


# --- history_backfill ---------------------------------------------------


def test_epoch_second_timestamps_are_parsed() -> None:
    """The live REST endpoint returns epoch seconds as a STRING. An
    ISO-only parser silently dropped every row, turning a 7,500-bar fetch
    into 0 bars written."""
    parsed = _parse_ts("1780285500")

    assert parsed is not None
    assert parsed == dt.datetime(2026, 6, 1, 3, 45, tzinfo=dt.UTC)  # 09:15 IST, the open
    assert parsed.astimezone(IST).strftime("%H:%M") == "09:15"


def test_iso_timestamps_are_also_parsed() -> None:
    """Some clients hand back ISO for the same data — accept both rather
    than assume whichever shape was observed first."""
    assert _parse_ts("2025-09-02T09:54:00.000Z") == dt.datetime(2025, 9, 2, 9, 54, tzinfo=dt.UTC)


@pytest.mark.parametrize("raw", ["", "0", "-1", "not-a-timestamp"])
def test_unusable_timestamps_drop_their_row_rather_than_becoming_epoch_zero(raw: str) -> None:
    """A 0 here is how a `month=1970-01` partition appears in the store."""
    assert _parse_ts(raw) is None


def test_backfilled_bars_are_visible_to_a_point_in_time_read(tmp_path: Path) -> None:
    """THE reason `ingested_at` is stamped at close time rather than now:
    `bars_asof` admits a bar only when `ingested_at <= as_of`, so a backfill
    stamped `now()` would be invisible to every historical replay and the
    replay would silently produce zero signals."""

    store = BarStore(tmp_path / "bars")
    result = backfill_index_bars(
        _OneBarClient(),  # type: ignore[arg-type]
        store,
        symbol=SYMBOL,
        exchange=EXCHANGE,
        start=dt.date(2026, 6, 1),
        end=dt.date(2026, 6, 1),
    )

    assert result.ok
    assert result.bars_written == 1

    from te.data.asof import bars_asof

    as_of = dt.datetime(2026, 6, 1, 3, 47, tzinfo=dt.UTC)  # two minutes after the bar closed
    visible = bars_asof(store, SYMBOL, as_of, dt.timedelta(hours=1))
    assert len(visible) == 1, "backfilled bar was invisible to a point-in-time read"


def test_a_backfilled_bar_is_distinguishable_from_a_live_recorded_one(tmp_path: Path) -> None:
    store = BarStore(tmp_path / "bars")
    backfill_index_bars(
        _OneBarClient(),  # type: ignore[arg-type]
        store, symbol=SYMBOL, exchange=EXCHANGE, start=dt.date(2026, 6, 1), end=dt.date(2026, 6, 1),
    )
    frame = store.read(
        symbol=SYMBOL,
        start=dt.datetime(2026, 6, 1, tzinfo=dt.UTC),
        end=dt.datetime(2026, 6, 2, tzinfo=dt.UTC),
        interval="1m",
    )
    assert frame["source"].unique().tolist() == [SOURCE]


def test_month_windows_cover_the_range_without_gaps_or_overlap() -> None:
    windows = month_windows(dt.date(2025, 9, 15), dt.date(2025, 12, 3))

    assert windows[0] == (dt.date(2025, 9, 15), dt.date(2025, 9, 30))
    assert windows[-1] == (dt.date(2025, 12, 1), dt.date(2025, 12, 3))
    for (_, prev_end), (next_start, _) in zip(windows, windows[1:], strict=False):
        assert next_start == prev_end + dt.timedelta(days=1)


# --- replay -------------------------------------------------------------


def test_the_day_cache_store_preserves_the_point_in_time_gate(breakout_store: BarStore, tmp_path: Path) -> None:
    """`_DayCacheStore` may replace disk access, never the lookahead
    defence: a read as of the opening-range end must not see the breakout
    bar that closes afterwards."""
    day = dt.date(2026, 6, 2)
    frame = breakout_store.read(
        symbol=SYMBOL,
        start=_open(day, 0) - dt.timedelta(hours=1),
        end=_open(day, 60),
        interval="1m",
    )
    cached = _DayCacheStore(tmp_path, frame)

    from te.data.asof import bars_asof

    visible = bars_asof(cached, SYMBOL, _open(day, 15), dt.timedelta(hours=7))

    assert len(visible) == 15, "a bar that had not closed yet leaked into a point-in-time read"


def test_replay_records_a_firing_the_labeler_can_consume(
    breakout_store: BarStore, session_factory  # noqa: ANN001
) -> None:
    """The firing must land in `cycle_evaluations` with `verdict='traded'` —
    the exact rows `te.ml.labeling.label_firings` selects on."""
    result = replay_orb(
        store=breakout_store,
        session_factory=session_factory,
        instruments={SYMBOL: EXCHANGE},
        start=dt.date(2026, 6, 2),
        end=dt.date(2026, 6, 2),
    )

    assert result.firings == 1, f"expected exactly one crossing, got {result.firings}"
    with session_factory() as session:
        rows = session.query(CycleEvaluationRow).filter_by(verdict="traded").all()
    assert len(rows) == 1
    assert rows[0].instrument == SYMBOL


def test_replay_is_idempotent(breakout_store: BarStore, session_factory) -> None:  # noqa: ANN001
    """`evaluation_id` is UNIQUE and derived from (strategy, instrument,
    as_of), so a re-run over an already-replayed window would abort on a
    constraint violation. A long backfill must be resumable after a broker
    timeout."""
    kwargs = {
        "store": breakout_store,
        "session_factory": session_factory,
        "instruments": {SYMBOL: EXCHANGE},
        "start": dt.date(2026, 6, 2),
        "end": dt.date(2026, 6, 2),
    }
    replay_orb(**kwargs)  # type: ignore[arg-type]
    replay_orb(**kwargs)  # type: ignore[arg-type]

    with session_factory() as session:
        assert session.query(CycleEvaluationRow).filter_by(verdict="traded").count() == 1


def test_replay_is_tagged_replay_never_paper(breakout_store: BarStore, session_factory) -> None:  # noqa: ANN001
    """A replayed firing must always be distinguishable from one the live
    engine actually took."""
    from te.persistence.models import CycleRow

    replay_orb(
        store=breakout_store, session_factory=session_factory, instruments={SYMBOL: EXCHANGE},
        start=dt.date(2026, 6, 2), end=dt.date(2026, 6, 2),
    )

    with session_factory() as session:
        assert [r.mode for r in session.query(CycleRow).all()] == ["replay"]


def test_replay_does_not_enter_after_the_hard_exit_time(tmp_path: Path, session_factory) -> None:  # noqa: ANN001
    """A firing at 15:25 would be force-closed on the same live cycle, so it
    must not become a training row."""
    store = BarStore(tmp_path / "bars")
    day = dt.date(2026, 6, 2)
    rows = [_bar(_open(day, m), o=100, h=101, low=99, c=100) for m in range(60)]
    # Flat until after 15:20 IST (minute 365), then a late crossing.
    rows += [_bar(_open(day, m), o=100, h=101, low=99, c=100) for m in range(60, 368)]
    rows.append(_bar(_open(day, 368), o=100, h=110, low=100, c=109))  # 15:23 IST
    store.append(pd.DataFrame(rows, columns=list(BAR_COLUMNS)))

    result = replay_orb(
        store=store, session_factory=session_factory, instruments={SYMBOL: EXCHANGE}, start=day, end=day
    )

    assert result.firings == 0, "a post-15:20 crossing became a training sample"


def test_the_opening_range_length_actually_reaches_the_rule(tmp_path: Path, session_factory) -> None:  # noqa: ANN001
    """`replay_orb(opening_range_minutes=...)` alone only moves the first
    evaluable minute — the rule is built by the registry with its DEFAULT
    15-minute range. Sweeping the parameter without a matching
    `strategy_factory` would produce identical firings at every value and
    read as "opening range length does not matter".

    Built so the two lengths MUST disagree: the first 5 minutes are flat, then
    price steps up and holds. A 5-minute range is broken by that step; a
    30-minute range absorbs it into the range itself.
    """
    from te.strategy.orb import OrbParams, OrbStrategy

    store = BarStore(tmp_path / "bars")
    day = dt.date(2026, 6, 2)
    rows = [_bar(_open(day, m), o=100, h=101, low=99, c=100) for m in range(5)]
    rows += [_bar(_open(day, m), o=104, h=106, low=103, c=105) for m in range(5, 40)]
    store.append(pd.DataFrame(rows, columns=list(BAR_COLUMNS)))

    def _replay(minutes: int) -> int:
        return replay_orb(
            store=store,
            session_factory=session_factory,
            instruments={SYMBOL: EXCHANGE},
            start=day,
            end=day,
            strategy_factory=lambda: OrbStrategy(OrbParams(opening_range_minutes=minutes, min_opening_bars=3)),
            opening_range_minutes=minutes,
        ).firings

    short_range = _replay(5)
    long_range = _replay(30)

    assert short_range != long_range, (
        f"both range lengths produced {short_range} firings — the swept value never reached the rule"
    )


def test_the_replay_entry_cutoff_matches_what_the_live_engine_would_accept() -> None:
    """`DEFAULT_LAST_ENTRY` must equal the live engine's last tradeable
    minute, or the training set contains firings the engine would refuse.

    That divergence is silent and self-reinforcing: the model learns from
    late-day trades it will never be offered, and every win rate derived
    from those labels describes a strategy nobody runs. It went unnoticed
    once already — the constant stayed at 15:20 after `hard_exit_by` moved
    to 15:15 and the runway rule went from 0 to 40 minutes on 2026-08-01.
    """
    import datetime as dt

    from te.backtest.replay import DEFAULT_LAST_ENTRY
    from te.settings import Settings

    settings = Settings()
    hard_exit = dt.datetime.combine(dt.date(2026, 8, 3), settings.paper_cycle_hard_exit_by)
    last_entry = hard_exit - dt.timedelta(minutes=settings.paper_cycle_min_minutes_before_hard_exit)

    assert DEFAULT_LAST_ENTRY == last_entry.time(), (
        f"replay accepts entries until {DEFAULT_LAST_ENTRY}, but the live engine stops at "
        f"{last_entry.time()} (hard exit {settings.paper_cycle_hard_exit_by} minus "
        f"{settings.paper_cycle_min_minutes_before_hard_exit}m runway)"
    )
