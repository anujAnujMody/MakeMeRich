"""Two data-integrity fixes made on 2026-08-01.

**Archive index bars.** The option archive carries a `*_spot.csv` index
series alongside the contracts. It was skipped, on the reasoning that the
store already holds live-recorded index bars under the same `NIFTY` key and
mixing two vendors leaves no way to tell them apart. Correct concern, wrong
conclusion: without the archive's index there are no bars for `te.strategy.
orb` to find an opening range in before 2025-09-01, so two years of real
option premiums sit in the store unreachable.

The fix keeps the concern intact — a `spot_cutoff` truncates the archive
series strictly before the first live-recorded bar, so the overlap is empty
BY CONSTRUCTION rather than by hoping two vendors agree to the paisa.

**Implausible timestamps.** A live NIFTY bar was found stamped
1970-01-01T00:29Z with real-looking prices, written by the WS recorder on
2026-07-30. A tick with a garbage epoch was believed without question.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pandas as pd
import pytest

from te.data.barstore import BAR_COLUMNS, BarStore

CUTOFF = dt.date(2025, 9, 1)


def _bar(*, symbol: str = "NIFTY", event_ts: dt.datetime) -> dict[str, object]:
    return {
        "symbol": symbol,
        "exchange": "NSE_INDEX",
        "event_ts": event_ts,
        "interval": "1m",
        "o": 24_000.0,
        "h": 24_010.0,
        "l": 23_990.0,
        "c": 24_005.0,
        "v": 0,
        "oi": 0,
        "ingested_at": event_ts + dt.timedelta(minutes=1),
        "source": "test",
    }


@pytest.fixture
def store(tmp_path: Path) -> BarStore:
    return BarStore(tmp_path / "bars")


# --- implausible timestamps -------------------------------------------------


def test_a_zero_epoch_bar_is_refused(store: BarStore) -> None:
    """The exact bug, reproduced: 1970-01-01T00:29Z with plausible prices."""
    frame = pd.DataFrame([_bar(event_ts=dt.datetime(1970, 1, 1, 0, 29, tzinfo=dt.UTC))])
    with pytest.raises(ValueError, match="implausible event_ts"):
        store.append(frame.loc[:, list(BAR_COLUMNS)])


def test_a_far_future_timestamp_is_refused(store: BarStore) -> None:
    """The upper bound.

    Note what it does NOT catch, because it cannot: a millisecond epoch read
    as seconds lands around the year 57,000, which pandas cannot represent
    at all (its nanosecond backing overflows past 2262) — that corruption
    raises at parse time, before it ever reaches this guard. The bound is
    therefore for lesser damage, e.g. a mis-parsed year field."""
    frame = pd.DataFrame([_bar(event_ts=dt.datetime(2200, 1, 1, tzinfo=dt.UTC))])
    with pytest.raises(ValueError, match="implausible event_ts"):
        store.append(frame.loc[:, list(BAR_COLUMNS)])


def test_one_bad_bar_refuses_the_whole_write_rather_than_dropping_it(store: BarStore) -> None:
    """Raising, not silently dropping. A quiet drop would hide a broken feed
    exactly as thoroughly as the bad bar itself did — and the write is
    all-or-nothing so no half-batch lands."""
    good = _bar(event_ts=dt.datetime(2026, 7, 30, 4, 0, tzinfo=dt.UTC))
    bad = _bar(event_ts=dt.datetime(1970, 1, 1, 0, 29, tzinfo=dt.UTC))
    frame = pd.DataFrame([good, bad]).loc[:, list(BAR_COLUMNS)]

    with pytest.raises(ValueError):
        store.append(frame)

    read = store.read(
        symbol="NIFTY",
        start=dt.datetime(2026, 7, 1, tzinfo=dt.UTC),
        end=dt.datetime(2026, 8, 1, tzinfo=dt.UTC),
        interval="1m",
    )
    assert read.empty, "a refused batch must not have written its good rows either"


def test_ordinary_bars_including_future_dated_fixtures_still_write(store: BarStore) -> None:
    """The guard is about impossible EPOCHS, not about whether a bar is in
    the future. `te.data.asof` owns point-in-time visibility; duplicating
    that rule here would put it in two places and break every test that
    writes a synthetic forward date."""
    ahead = dt.datetime.now(dt.UTC) + dt.timedelta(days=40)
    written = store.append(pd.DataFrame([_bar(event_ts=ahead)]).loc[:, list(BAR_COLUMNS)])
    assert written == 1


# --- archive spot series ----------------------------------------------------


def test_spot_rows_after_the_cutoff_are_dropped() -> None:
    """The no-overlap guarantee. Anything from the cutoff onward is the live
    recorder's territory and must not be duplicated from a second vendor."""
    from te.data.option_history import _spot_frame

    raw = pd.DataFrame(
        {
            "Timestamp": ["29-08-2025 09:15:00", "01-09-2025 09:15:00", "02-09-2025 09:15:00"],
            "Open": [24_000.0] * 3,
            "High": [24_010.0] * 3,
            "Low": [23_990.0] * 3,
            "Close": [24_005.0] * 3,
        }
    )
    frame = _spot_frame(raw, base="NIFTY", interval_span=dt.timedelta(minutes=1), cutoff=CUTOFF)

    assert len(frame) == 1, "only the 29-Aug bar precedes the 1-Sep cutoff"
    assert frame["event_ts"].max() < pd.Timestamp(CUTOFF, tz="UTC")


def test_spot_bars_are_tagged_with_their_own_source() -> None:
    """Same symbol key as the live recorder, so the source column is the only
    thing that can ever tell the two apart afterwards."""
    from te.data.option_history import SPOT_SOURCE, _spot_frame

    raw = pd.DataFrame(
        {
            "Timestamp": ["29-08-2025 09:15:00"],
            "Open": [24_000.0],
            "High": [24_010.0],
            "Low": [23_990.0],
            "Close": [24_005.0],
        }
    )
    frame = _spot_frame(raw, base="NIFTY", interval_span=dt.timedelta(minutes=1), cutoff=CUTOFF)

    assert frame.loc[0, "source"] == SPOT_SOURCE
    assert frame.loc[0, "symbol"] == "NIFTY"


def test_an_index_gets_its_index_exchange_not_the_derivative_one() -> None:
    """`NSE_INDEX`, not `NFO`. Conflating the two is what made the pre-fix
    engine try to place an order for `NIFTY` on the derivatives exchange."""
    from te.data.option_history import _spot_frame

    raw = pd.DataFrame(
        {
            "Timestamp": ["29-08-2025 09:15:00"],
            "Open": [24_000.0],
            "High": [24_010.0],
            "Low": [23_990.0],
            "Close": [24_005.0],
        }
    )
    assert _spot_frame(
        raw, base="NIFTY", interval_span=dt.timedelta(minutes=1), cutoff=CUTOFF
    ).loc[0, "exchange"] == "NSE_INDEX"


def test_an_index_reports_zero_volume_honestly() -> None:
    """An index has no traded volume of its own — only its derivatives do.
    `te.strategy.orb` already reports that as "not evaluated" rather than a
    passed filter, which only works if the 0 arrives as a real 0."""
    from te.data.option_history import _spot_frame

    raw = pd.DataFrame(
        {
            "Timestamp": ["29-08-2025 09:15:00"],
            "Open": [24_000.0],
            "High": [24_010.0],
            "Low": [23_990.0],
            "Close": [24_005.0],
        }
    )
    frame = _spot_frame(raw, base="NIFTY", interval_span=dt.timedelta(minutes=1), cutoff=CUTOFF)
    assert int(frame.loc[0, "v"]) == 0
    assert int(frame.loc[0, "oi"]) == 0
