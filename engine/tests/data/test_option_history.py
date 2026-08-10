"""`te.data.option_history` — the Shoonya historical option archive loader.

The two tests that actually matter here are `test_ingested_at_is_the_bars_own
_close_not_now` (a `now()` stamp would make every historical bar visible to
every as-of query, destroying the lookahead defence for the whole archive)
and `test_reloading_the_same_archive_writes_nothing_twice` (the store is
append-only with randomised part filenames, so a non-idempotent loader
silently doubles every premium path on a re-run).
"""

from __future__ import annotations

import datetime as dt
import io
import zipfile
from pathlib import Path

import pytest

from te.data.asof import bars_asof
from te.data.barstore import BarStore
from te.data.option_history import (
    available_option_symbols,
    default_manifest_path,
    load_option_history,
)
from te.domain.clock import IST

HEADER = "Date,Timestamp,Open,High,Low,Close,Volume,OI,Ticker"


def _csv(ticker: str, day: dt.date, rows: int = 3) -> str:
    lines = [HEADER]
    for i in range(rows):
        stamp = dt.datetime(day.year, day.month, day.day, 9, 15 + i)
        lines.append(
            f"{day.isoformat()},{stamp.strftime('%d-%m-%Y %H:%M:%S')},"
            f"{100 + i}.0,{102 + i}.0,{99 + i}.0,{101 + i}.0,{500 + i},{1000 + i},{ticker}"
        )
    return "\n".join(lines) + "\n"


def _inner_zip(entries: dict[str, str]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        for name, content in entries.items():
            zf.writestr(name, content)
    return buffer.getvalue()


def _archive(path: Path, inner: dict[str, bytes]) -> Path:
    with zipfile.ZipFile(path, "w") as zf:
        for name, payload in inner.items():
            zf.writestr(name, payload)
    return path


@pytest.fixture
def archive(tmp_path: Path) -> Path:
    """Two expiries, deliberately in the archive's TWO real layouts: the
    2024 one is flat with `CE`/`PE` tickers, the 2026 one is nested under a
    directory with single-letter `C`/`P` tickers. Both forms exist in the
    real 670 MB file."""
    flat = _inner_zip(
        {
            "21650CE_20240104.csv": _csv("NIFTY04JAN24CE21650", dt.date(2024, 1, 3)),
            "21650PE_20240104.csv": _csv("NIFTY04JAN24PE21650", dt.date(2024, 1, 3)),
            "nifty_spot.csv": _csv("Nifty 50", dt.date(2024, 1, 3)),
        }
    )
    nested = _inner_zip(
        {
            "20260505/": "",
            "20260505/24500CE_20260505.csv": _csv("NIFTY05MAY26C24500", dt.date(2026, 5, 4)),
            "20260505/nifty_spot.csv": _csv("Nifty 50", dt.date(2026, 5, 4)),
        }
    )
    return _archive(tmp_path / "nifty_all.zip", {"/": b"", "20240104.zip": flat, "20260505.zip": nested})


@pytest.fixture
def store(tmp_path: Path) -> BarStore:
    return BarStore(tmp_path / "bars")


def test_loads_both_archive_layouts_under_canonical_symbols(archive: Path, store: BarStore) -> None:
    result = load_option_history(archive, store)

    assert result.contracts_loaded == 3  # spot files are not option contracts
    assert result.bars_written == 9
    assert {p.symbol for p in available_option_symbols(store, "NIFTY")} == {
        "NIFTY04JAN2421650CE",
        "NIFTY04JAN2421650PE",
        "NIFTY05MAY2624500CE",
    }


def test_spot_file_is_never_written_as_an_option(archive: Path, store: BarStore) -> None:
    """`nifty_spot.csv` carries the INDEX, and the store already holds index
    bars recorded live. Writing the archive's copy under the same key would
    interleave two sources' bars for one symbol with no way to tell them
    apart."""
    load_option_history(archive, store)
    assert not (store.root / "interval=1m" / "symbol=NIFTY").exists()


def test_ingested_at_is_the_bars_own_close_not_now(archive: Path, store: BarStore) -> None:
    load_option_history(archive, store)
    df = store.read(
        "NIFTY05MAY2624500CE",
        dt.datetime(2026, 5, 1, tzinfo=dt.UTC),
        dt.datetime(2026, 5, 6, tzinfo=dt.UTC),
        "1m",
    )
    assert len(df) == 3
    assert (df["ingested_at"] - df["event_ts"] == dt.timedelta(minutes=1)).all()
    first = df.iloc[0]
    assert first["event_ts"].to_pydatetime() == dt.datetime(2026, 5, 4, 9, 15, tzinfo=IST)
    assert float(first["o"]) == 100.0
    assert float(first["h"]) == 102.0
    assert int(first["oi"]) == 1000


def test_a_historical_bar_is_invisible_to_an_asof_before_its_close(archive: Path, store: BarStore) -> None:
    """The whole point of the close-time `ingested_at`: a 09:15 bar must not
    be visible at 09:15:30, even though we loaded it years later."""
    load_option_history(archive, store)
    mid_bar = dt.datetime(2026, 5, 4, 9, 15, 30, tzinfo=IST)
    assert bars_asof(store, "NIFTY05MAY2624500CE", mid_bar, dt.timedelta(hours=1)).empty
    after = dt.datetime(2026, 5, 4, 9, 16, tzinfo=IST)
    assert len(bars_asof(store, "NIFTY05MAY2624500CE", after, dt.timedelta(hours=1))) == 1


def test_reloading_the_same_archive_writes_nothing_twice(archive: Path, store: BarStore) -> None:
    load_option_history(archive, store)
    second = load_option_history(archive, store)

    assert second.bars_written == 0
    assert second.contracts_skipped == 3
    df = store.read(
        "NIFTY04JAN2421650CE",
        dt.datetime(2024, 1, 1, tzinfo=dt.UTC),
        dt.datetime(2024, 1, 5, tzinfo=dt.UTC),
        "1m",
    )
    assert len(df) == 3


def test_spot_cutoff_loads_the_index_series_idempotently(archive: Path, store: BarStore) -> None:
    """`spot_cutoff` is passed by exactly one real caller
    (`scripts/load_option_history.py`) and reached by no other test in this
    file — every other test omits it, which routes `nifty_spot.csv` down the
    "not a contract, skip" branch and never exercises `_spot_frame` or its
    per-`(base, expiry)` idempotency guard at all.

    The archive fixture's two expiries each carry their own `nifty_spot.csv`
    (3 rows apiece, non-overlapping calendar days), so a `spot_cutoff` after
    both must load 6 NIFTY index bars — and a second, identical load must
    still find exactly 6, not 12. Consecutive expiries' spot files overlap in
    the real archive; without per-file idempotency the same index minute
    would be appended once per expiry that mentions it, landing twice in one
    ORB opening range from an append-only store nothing can de-duplicate
    afterward."""
    cutoff = dt.date(2026, 5, 5)

    first = load_option_history(archive, store, spot_cutoff=cutoff)
    assert first.bars_written == 9 + 6  # 9 option bars (as in the no-cutoff case) + 6 spot bars

    df = store.read(
        "NIFTY",
        dt.datetime(2020, 1, 1, tzinfo=dt.UTC),
        dt.datetime(2030, 1, 1, tzinfo=dt.UTC),
        "1m",
    )
    assert len(df) == 6

    second = load_option_history(archive, store, spot_cutoff=cutoff)
    assert second.bars_written == 0, "a re-run of the backfill must not duplicate the index series"

    df_again = store.read(
        "NIFTY",
        dt.datetime(2020, 1, 1, tzinfo=dt.UTC),
        dt.datetime(2030, 1, 1, tzinfo=dt.UTC),
        "1m",
    )
    assert len(df_again) == 6, "reloading the same archive must not double the NIFTY partition"


def test_resumes_after_a_partial_load(archive: Path, store: BarStore) -> None:
    """A crash mid-archive must leave the completed contracts recorded, so a
    re-run costs only the unfinished ones."""
    load_option_history(archive, store, end=dt.date(2024, 1, 4))
    lines = default_manifest_path(store).read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2

    result = load_option_history(archive, store)
    assert result.contracts_loaded == 1
    assert result.contracts_skipped == 2
    assert result.bars_written == 3


def test_expiry_range_filters_which_inner_zips_are_opened(archive: Path, store: BarStore) -> None:
    result = load_option_history(archive, store, start=dt.date(2026, 1, 1))
    assert result.expiries_loaded == 1
    assert result.expiries_skipped == 1
    assert {p.symbol for p in available_option_symbols(store, "NIFTY")} == {"NIFTY05MAY2624500CE"}


def test_dry_run_counts_without_writing(archive: Path, store: BarStore) -> None:
    result = load_option_history(archive, store, dry_run=True)
    assert result.contracts_loaded == 3
    assert result.bars_written == 9
    assert not store.root.exists()
    assert not default_manifest_path(store).exists()


def test_rejects_a_csv_whose_ticker_contradicts_its_filename(tmp_path: Path, store: BarStore) -> None:
    """Filename and ticker are two independent statements of the same
    contract. If they disagree, one of them is wrong and there is no honest
    way to pick — bars filed under a wrong strike would silently become
    another contract's premium path."""
    inner = _inner_zip({"21650CE_20240104.csv": _csv("NIFTY04JAN24CE21700", dt.date(2024, 1, 3))})
    path = _archive(tmp_path / "bad.zip", {"20240104.zip": inner})
    with pytest.raises(ValueError, match="disagrees"):
        load_option_history(path, store)


def test_available_option_symbols_is_empty_for_an_unloaded_base(archive: Path, store: BarStore) -> None:
    load_option_history(archive, store)
    assert available_option_symbols(store, "BANKNIFTY") == []


def _csv_with_untraded_prefix(ticker: str, day: dt.date, *, untraded: int, traded: int) -> str:
    """The real BANKNIFTY archive shape: the file opens with placeholder rows
    for minutes the contract had not yet traded — every price column AND
    Volume are blank, OI is published as 0 — then real bars follow."""
    lines = [HEADER]
    base = dt.datetime(day.year, day.month, day.day, 9, 15)
    for i in range(untraded):
        stamp = base + dt.timedelta(minutes=i)
        lines.append(f"{day.isoformat()},{stamp.strftime('%d-%m-%Y %H:%M:%S')},,,,,,0,{ticker}")
    for i in range(traded):
        stamp = base + dt.timedelta(minutes=untraded + i)
        lines.append(
            f"{day.isoformat()},{stamp.strftime('%d-%m-%Y %H:%M:%S')},"
            f"{100 + i}.0,{102 + i}.0,{99 + i}.0,{101 + i}.0,{500 + i},{1000 + i},{ticker}"
        )
    return "\n".join(lines) + "\n"


def test_minutes_the_contract_never_traded_are_dropped_not_zero_filled(tmp_path: Path, store: BarStore) -> None:
    """The BANKNIFTY archive carries a row per session minute whether or not
    the contract traded, and an untraded minute is blank in every price
    column and in Volume. Loading it used to die inside pandas'
    `astype("int64")` with no file name and no row count.

    They must be DROPPED. Filling `v=0` would manufacture a bar with no
    prices, and would additionally feed ORB's volume-confirmation average —
    silently dragging the threshold down with minutes that never traded.
    """
    inner = _inner_zip(
        {
            "20260428/55000CE_20260428.csv": _csv_with_untraded_prefix(
                "BANKNIFTY28APR26C55000", dt.date(2026, 4, 21), untraded=61, traded=3
            )
        }
    )
    archive = _archive(tmp_path / "banknifty_all.zip", {"20260428.zip": inner})

    result = load_option_history(archive, store)

    assert result.bars_written == 3, "the 61 untraded minutes were written instead of dropped"
    frame = store.read(
        symbol="BANKNIFTY28APR2655000CE",
        start=dt.datetime(2026, 4, 1, tzinfo=dt.UTC),
        end=dt.datetime(2026, 5, 1, tzinfo=dt.UTC),
        interval="1m",
    )
    assert len(frame) == 3
    assert (frame["v"] > 0).all(), "a zero-volume placeholder bar reached the store"


def test_a_contract_that_never_traded_at_all_loads_as_empty(tmp_path: Path, store: BarStore) -> None:
    """The degenerate case of the above — every row a placeholder. Must
    produce no bars rather than raising, since a strike listed but never
    traded is ordinary."""
    inner = _inner_zip(
        {
            "20260428/99000CE_20260428.csv": _csv_with_untraded_prefix(
                "BANKNIFTY28APR26C99000", dt.date(2026, 4, 21), untraded=5, traded=0
            )
        }
    )
    archive = _archive(tmp_path / "banknifty_all.zip", {"20260428.zip": inner})

    result = load_option_history(archive, store)

    assert result.bars_written == 0


def test_a_priced_bar_missing_volume_is_refused_by_name(tmp_path: Path, store: BarStore) -> None:
    """The boundary check. A row with real OHLC and no Volume is UNKNOWN
    data, not an absent trade — so it must fail loudly, naming the file and
    the column, instead of being dropped as a placeholder or filled with a
    number indistinguishable from a real one downstream."""
    rows = [
        HEADER,
        "2026-04-21,21-04-2026 10:15:00,100.0,102.0,99.0,101.0,,1000,BANKNIFTY28APR26C55000",
    ]
    inner = _inner_zip({"20260428/55000CE_20260428.csv": "\n".join(rows) + "\n"})
    archive = _archive(tmp_path / "banknifty_all.zip", {"20260428.zip": inner})

    with pytest.raises(ValueError, match="have no Volume"):
        load_option_history(archive, store)
