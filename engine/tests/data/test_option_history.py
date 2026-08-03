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
