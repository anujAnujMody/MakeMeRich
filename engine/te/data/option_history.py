"""Loads the Shoonya historical option archive into the Parquet `BarStore`.

### Why this exists

`te.ml.barriers` converts an index point move into an option premium move
using ONE day's delta snapshot, with no theta and no IV dynamics. Every
barrier outcome and every win rate built on it is therefore optimistically
biased, and that bias is invisible in the numbers themselves. This module is
the fix: real 1-minute option premiums, so a barrier can be walked on the
contract that would actually have been traded.

### The archive's shape (verified by reading it, not assumed)

`data/raw/shoonya/nifty_all.zip` is an outer zip of 125 inner zips named
`YYYYMMDD.zip` — the EXPIRY date — each holding per-strike CSVs plus one
`*_spot.csv`. Three things about it contradict a naive reader and each has
cost a test here:

* **Two inner layouts.** Most inner zips are flat (`21650CE_20240104.csv`);
  some (e.g. NIFTY `20260505.zip`, every BANKNIFTY one) nest everything under
  a `YYYYMMDD/` directory. Entry names are therefore matched on the basename.
* **Two ticker grammars.** `NIFTY04JAN24CE21650` up to expiry 2026-03-02,
  single-letter `NIFTY05MAY26C24500` from 2026-03-10 onward. Both are handled
  by `te.domain.symbols.parse_shoonya_option_symbol`.
* **Timestamps are IST wall clock** (`DD-MM-YYYY HH:MM:SS`), bar OPEN times,
  09:15..15:29 inclusive.

### Point-in-time correctness

`ingested_at` is the bar's own CLOSE time (`event_ts + interval`), never
`now()`. These bars genuinely existed one minute after they opened; stamping
them with the load time would make the entire archive visible to every
`bars_asof()` query regardless of `as_of`, which is precisely the
backfill-leak the two-timestamp scheme exists to prevent.

### Resumability

The store is append-only and its part filenames are randomised, so a re-run
would silently double every premium path. Completed contracts are recorded
one JSON line at a time in a manifest beside the store; the loader skips
anything already listed. Granularity is one contract, so a kill mid-archive
costs at most the contract in flight.
"""

from __future__ import annotations

import datetime as dt
import io
import json
import logging
import os
import re
import zipfile
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

import pandas as pd

from te.data.barstore import BAR_COLUMNS, BarStore
from te.domain.clock import IST
from te.domain.symbols import (
    FNO_UNDERLYING_EXCHANGES,
    OptionType,
    ParsedOptionSymbol,
    build_option_symbol,
    parse_option_symbol,
    parse_shoonya_option_symbol,
)

logger = logging.getLogger(__name__)

#: Inner zips are named for the expiry they cover.
_EXPIRY_ZIP_RE = re.compile(r"^(\d{8})\.zip$")
#: `<STRIKE><CE|PE>_<YYYYMMDD>.csv`, the archive's per-contract file name.
_CONTRACT_CSV_RE = re.compile(r"^(?P<strike>\d+(?:\.\d+)?)(?P<type>CE|PE)_(?P<expiry>\d{8})\.csv$")
_SPOT_CSV_RE = re.compile(r"^(?P<base>[a-z]+)_spot\.csv$", re.IGNORECASE)

_EXCHANGE_BY_BASE = dict(FNO_UNDERLYING_EXCHANGES)
#: An index quotes on `NSE_INDEX`/`BSE_INDEX`, NOT on the derivative
#: exchange its options are placed on — the same distinction that made the
#: pre-fix engine try to trade `NIFTY` on `NFO`.
_INDEX_EXCHANGE_BY_BASE = {"NIFTY": "NSE_INDEX", "BANKNIFTY": "NSE_INDEX",
                           "SENSEX": "BSE_INDEX", "BANKEX": "BSE_INDEX"}

SOURCE = "shoonya-archive"
#: Distinct from `SOURCE` so an index bar that came from the archive is
#: always tellable from one this engine recorded live, even though both land
#: under the same `NIFTY` symbol key. See `spot_cutoff` for why they can
#: never overlap in the first place.
SPOT_SOURCE = "shoonya-archive-spot"

_TIMESTAMP_FORMAT = "%d-%m-%Y %H:%M:%S"


@dataclass(frozen=True)
class LoadResult:
    """What a run actually did. Reported verbatim by the CLI — a load that
    skipped everything must be legible as a skip, not as a success."""

    expiries_loaded: int = 0
    expiries_skipped: int = 0
    contracts_loaded: int = 0
    contracts_skipped: int = 0
    bars_written: int = 0


def default_manifest_path(store: BarStore) -> Path:
    """Beside the store, not inside a partition: `BarStore.read` globs
    `interval=*/symbol=*/month=*`, so an `_ingest/` sibling is invisible to
    every reader."""
    return store.root / "_ingest" / "option_history.jsonl"


def _load_manifest(path: Path) -> set[str]:
    if not path.exists():
        return set()
    done: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            done.add(str(json.loads(line)["symbol"]))
        except (json.JSONDecodeError, KeyError):
            # A torn final line is the expected shape of a kill mid-append.
            # Dropping it means that one contract reloads; keeping the rest
            # of the manifest is what makes the run resumable at all.
            logger.warning("ignoring unparseable manifest line in %s", path)
    return done


def _record(path: Path, symbol: str, rows: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    entry = {"symbol": symbol, "rows": rows, "loaded_at": dt.datetime.now(dt.UTC).isoformat()}
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def _expiry_zips(archive: zipfile.ZipFile) -> Iterator[tuple[dt.date, str]]:
    for name in sorted(archive.namelist()):
        match = _EXPIRY_ZIP_RE.match(Path(name).name)
        if match is None:
            continue
        raw = match.group(1)
        yield dt.date(int(raw[:4]), int(raw[4:6]), int(raw[6:8])), name


def _contract_frame(raw: pd.DataFrame, *, csv_name: str, interval_span: dt.timedelta) -> tuple[str, pd.DataFrame]:
    """One contract CSV -> `(canonical_symbol, bar frame)`."""
    tickers = raw["Ticker"].unique()
    if len(tickers) != 1:
        raise ValueError(f"{csv_name}: expected one ticker per file, found {sorted(tickers)}")
    parsed = parse_shoonya_option_symbol(str(tickers[0]))

    # Filename and ticker are two independent statements of the same
    # contract. Trusting either alone is a silent-corruption risk: a
    # mismatched strike files real premiums under a contract that never
    # traded, and nothing downstream can tell.
    name_match = _CONTRACT_CSV_RE.match(Path(csv_name).name)
    if name_match is None:
        raise ValueError(f"{csv_name}: not a recognised contract file name")
    expiry_raw = name_match.group("expiry")
    from_name = (
        float(name_match.group("strike")),
        name_match.group("type"),
        dt.date(int(expiry_raw[:4]), int(expiry_raw[4:6]), int(expiry_raw[6:8])),
    )
    from_ticker = (float(parsed.strike), parsed.option_type, parsed.expiry)
    if from_name != from_ticker:
        raise ValueError(f"{csv_name}: ticker {parsed.symbol!r} disagrees with the file name ({from_name})")

    symbol = build_option_symbol(parsed.base, parsed.expiry, parsed.strike, parsed.option_type)

    # NO-TRADE PLACEHOLDER ROWS. The archive emits a row per minute of the
    # session whether or not the contract traded, and an untraded minute
    # carries NaN for all four prices AND for volume (OI is published as 0).
    # Found in the BANKNIFTY archive, which the NIFTY one never exercised:
    # 366 such rows out of 6,268,086, e.g. 61 consecutive minutes from
    # 14:29 on 2026-04-21 on `BANKNIFTY28APR26C55000` before that contract
    # first traded.
    #
    # Dropped, not filled. `v=0` would manufacture a bar with no prices —
    # `astype("int64")` is what raised on it, which is the schema refusing
    # to store a bar that does not exist. A zero-volume bar would also feed
    # ORB's volume-confirmation average and quietly drag it down.
    #
    # Volume is only tolerated as missing when the PRICES are missing too.
    # A row with real OHLC and no volume would be a genuinely unknown
    # quantity rather than an absent trade, and is left to raise below
    # rather than guessed at.
    priced = raw.dropna(subset=["Open", "High", "Low", "Close"])
    if priced.empty:
        return symbol, pd.DataFrame(columns=list(BAR_COLUMNS))
    raw = priced

    # VALIDATE AT THE INGESTION BOUNDARY, loudly and by name.
    #
    # Before this, a supplier file with an unexpected NaN surfaced as
    # `IntCastingNaNError: Cannot convert non-finite values` thrown from
    # inside pandas' astype machinery — no file name, no column, no row
    # count, and a traceback that pointed at pandas rather than at the data.
    # Diagnosing it meant unzipping the archive by hand.
    #
    # Anything still missing here is NOT a no-trade minute (those are gone
    # above), so it is genuinely unknown data. Refuse it, and say exactly
    # which file and column, rather than guessing a value that would be
    # indistinguishable from a real one downstream.
    for column in ("Volume", "OI"):
        missing = int(raw[column].isna().sum())
        if missing:
            raise ValueError(
                f"{csv_name}: {missing} of {len(raw)} priced rows have no {column}. "
                f"A bar with real OHLC and no {column} is unknown data, not an absent "
                f"trade — refusing rather than substituting a value."
            )

    event_ts = pd.to_datetime(raw["Timestamp"], format=_TIMESTAMP_FORMAT).dt.tz_localize(IST).dt.tz_convert("UTC")

    frame = pd.DataFrame(
        {
            "symbol": symbol,
            "exchange": _EXCHANGE_BY_BASE.get(parsed.base, "NFO"),
            "event_ts": event_ts,
            "interval": "1m",
            "o": raw["Open"].astype(float),
            "h": raw["High"].astype(float),
            "l": raw["Low"].astype(float),
            "c": raw["Close"].astype(float),
            "v": raw["Volume"].astype("int64"),
            # Passed through as published. The archive's OI column goes
            # NEGATIVE on some expiry-day bars (e.g. -70915 on
            # NIFTY05MAY26C24500's 15:29 bar), so it is an OI *change* there
            # rather than an absolute — recorded as given rather than
            # "corrected" into a number the vendor never published.
            "oi": raw["OI"].astype("int64"),
            # The bar's own close: this data genuinely existed then.
            "ingested_at": event_ts + interval_span,
            "source": SOURCE,
        }
    )
    return symbol, frame.loc[:, list(BAR_COLUMNS)].sort_values("event_ts").reset_index(drop=True)


def _spot_frame(
    raw: pd.DataFrame, *, base: str, interval_span: dt.timedelta, cutoff: dt.date
) -> pd.DataFrame:
    """One `*_spot.csv` -> index bar frame, truncated at `cutoff`.

    The archive's spot series is what makes a pre-2025-09 ORB replay possible
    at all: the rule needs INDEX bars to find its opening range, and this
    engine only started recording its own on 2025-09-01. Without this, two
    years of real option premiums sit in the store unreachable, because
    nothing can decide where a firing would have happened.

    `cutoff` is the load-bearing argument. Archive and live-recorded bars
    share the `NIFTY` symbol key, and `bars_asof` would happily return both
    for an overlapping minute — double-counting the same bar from two
    vendors, with different rounding, into one opening range. Truncating
    strictly BEFORE the first live-recorded bar makes the overlap empty by
    construction rather than by hoping the two agree.
    """
    event_ts = pd.to_datetime(raw["Timestamp"], format=_TIMESTAMP_FORMAT).dt.tz_localize(IST).dt.tz_convert("UTC")
    frame = pd.DataFrame(
        {
            "symbol": base,
            "exchange": _INDEX_EXCHANGE_BY_BASE.get(base, "NSE_INDEX"),
            "event_ts": event_ts,
            "interval": "1m",
            "o": raw["Open"].astype(float),
            "h": raw["High"].astype(float),
            "l": raw["Low"].astype(float),
            "c": raw["Close"].astype(float),
            # An index has no traded volume or OI of its own — only its
            # derivatives do. The archive publishes 0 here and it is passed
            # through as the honest 0 it is, which `te.strategy.orb` already
            # reports as "volume not evaluated" rather than a passed filter.
            "v": 0,
            "oi": 0,
            "ingested_at": event_ts + interval_span,
            "source": SPOT_SOURCE,
        }
    )
    cut = pd.Timestamp(cutoff, tz="UTC")
    frame = frame.loc[frame["event_ts"] < cut]
    return frame.loc[:, list(BAR_COLUMNS)].sort_values("event_ts").reset_index(drop=True)


def load_option_history(
    archive_path: Path | str,
    store: BarStore,
    *,
    manifest_path: Path | None = None,
    start: dt.date | None = None,
    end: dt.date | None = None,
    dry_run: bool = False,
    spot_cutoff: dt.date | None = None,
    progress: Callable[[str], None] | None = None,
) -> LoadResult:
    """Streams `archive_path` into `store`, one expiry (then one contract) at
    a time.

    `start`/`end` filter on the EXPIRY date the inner zip is named for, so a
    restricted run never decompresses the inner zips it does not need — the
    NIFTY archive is 670 MB and holding more than one expiry in memory at
    once is what makes this run out of it.

    Returns a `LoadResult`; `dry_run` counts and parses everything but writes
    neither bars nor manifest.
    """
    manifest = manifest_path or default_manifest_path(store)
    done = set() if dry_run else _load_manifest(manifest)
    interval_span = dt.timedelta(minutes=1)
    result = LoadResult()

    with zipfile.ZipFile(archive_path) as archive:
        for expiry, inner_name in _expiry_zips(archive):
            if (start is not None and expiry < start) or (end is not None and expiry > end):
                result = _bump(result, expiries_skipped=1)
                continue
            if progress is not None:
                progress(f"expiry {expiry.isoformat()}")

            # Read the inner zip's bytes ONCE into memory (~5 MB compressed)
            # and drop them at the end of the iteration. `zipfile` cannot
            # random-access a nested member through the outer stream, so this
            # buffer is unavoidable — but it is per-expiry, not per-archive.
            payload = archive.read(inner_name)
            result = _bump(result, expiries_loaded=1)
            with zipfile.ZipFile(io.BytesIO(payload)) as inner:
                for csv_name in sorted(inner.namelist()):
                    spot_match = _SPOT_CSV_RE.match(Path(csv_name).name)
                    if spot_match is not None and spot_cutoff is not None:
                        # The index series. Written only when a `spot_cutoff`
                        # is supplied, and only for bars strictly before it —
                        # see `_spot_frame`. Keyed per (base, expiry) in the
                        # manifest because consecutive expiries' spot files
                        # OVERLAP (each covers its own run-up), so without
                        # per-file idempotency the same minute would be
                        # appended once per expiry that mentions it.
                        base = spot_match.group("base").upper()
                        spot_key = f"{base}#spot@{expiry.isoformat()}"
                        if spot_key in done:
                            result = _bump(result, contracts_skipped=1)
                            continue
                        with inner.open(csv_name) as handle:
                            spot_raw = pd.read_csv(handle)
                        if spot_raw.empty:
                            continue
                        spot_frame = _spot_frame(
                            spot_raw, base=base, interval_span=interval_span, cutoff=spot_cutoff
                        )
                        if spot_frame.empty:
                            continue
                        if not dry_run:
                            store.append(spot_frame)
                            _record(manifest, spot_key, len(spot_frame))
                            done.add(spot_key)
                        result = _bump(result, contracts_loaded=1, bars_written=len(spot_frame))
                        continue
                    if _CONTRACT_CSV_RE.match(Path(csv_name).name) is None:
                        # Directory entries, and `*_spot.csv` when no
                        # `spot_cutoff` was given.
                        continue
                    with inner.open(csv_name) as handle:
                        raw = pd.read_csv(handle)
                    if raw.empty:
                        continue
                    symbol, frame = _contract_frame(raw, csv_name=csv_name, interval_span=interval_span)
                    if symbol in done:
                        result = _bump(result, contracts_skipped=1)
                        continue
                    written = len(frame) if dry_run else store.append(frame)
                    if not dry_run:
                        _record(manifest, symbol, written)
                        done.add(symbol)
                    result = _bump(result, contracts_loaded=1, bars_written=written)

    return result


def _bump(result: LoadResult, **deltas: int) -> LoadResult:
    return LoadResult(
        expiries_loaded=result.expiries_loaded + deltas.get("expiries_loaded", 0),
        expiries_skipped=result.expiries_skipped + deltas.get("expiries_skipped", 0),
        contracts_loaded=result.contracts_loaded + deltas.get("contracts_loaded", 0),
        contracts_skipped=result.contracts_skipped + deltas.get("contracts_skipped", 0),
        bars_written=result.bars_written + deltas.get("bars_written", 0),
    )


def available_option_symbols(store: BarStore, base: str, interval: str = "1m") -> list[ParsedOptionSymbol]:
    """Every option contract of `base` that physically has bars in `store`.

    Directory listing only — it reads no bar, so it is not a second
    bar-reading entry point (that remains `te.data.asof.bars_asof`). Callers
    resolve a firing's contract against THIS rather than against
    `next_weekly_expiry()`: NIFTY's weekly expiry moved Thursday -> Wednesday
    -> Tuesday over the archive's 2024-2026 span, so the current calendar
    would resolve a 2024 firing to a contract that never existed.
    """
    root = store.root / f"interval={interval}"
    if not root.exists():
        return []
    found: list[ParsedOptionSymbol] = []
    for entry in sorted(root.glob("symbol=*")):
        if not entry.is_dir():
            continue
        symbol = entry.name.removeprefix("symbol=")
        if not symbol.startswith(base):
            continue
        try:
            parsed = parse_option_symbol(symbol)
        except ValueError:
            continue  # index/future symbols share the directory
        if parsed.base == base:
            found.append(parsed)
    return found


class OptionContractIndex:
    """Which loaded contract a firing would have traded.

    Built ONCE per underlying and reused across every firing: the underlying
    listing is a directory scan over thousands of `symbol=` partitions, and
    doing it per firing is quadratic over exactly the table this is meant to
    grow with.
    """

    def __init__(self, store: BarStore, base: str, interval: str = "1m") -> None:
        self._base = base
        self._by_expiry: dict[tuple[dt.date, OptionType], list[ParsedOptionSymbol]] = {}
        for parsed in available_option_symbols(store, base, interval):
            self._by_expiry.setdefault((parsed.expiry, parsed.option_type), []).append(parsed)
        self._expiries = sorted({expiry for expiry, _ in self._by_expiry})

    @property
    def base(self) -> str:
        return self._base

    @property
    def expiries(self) -> list[dt.date]:
        return list(self._expiries)

    def nearest(
        self,
        *,
        on: dt.date,
        index_level: Decimal,
        option_type: OptionType,
        max_days_to_expiry: int = 7,
        strikes_out_of_the_money: int = 0,
    ) -> ParsedOptionSymbol | None:
        """The nearest-expiry contract at the requested distance from spot,
        or `None` if the archive covers nothing usable there.

        `max_days_to_expiry` is what stops a COVERAGE GAP from being papered
        over. Rolling to whatever expiry happens to be loaded next would
        quietly relabel a firing as a trade in a contract with a different
        theta profile — so a candidate further out than the underlying's own
        expiry cycle (7 days for a weekly, ~31 for a monthly-only name like
        BANKNIFTY) is refused. `None` is a first-class answer: the caller
        falls back to the index approximation and COUNTS the fallback.

        `strikes_out_of_the_money` steps N strikes AWAY from spot — up for a
        call, down for a put — where 0 (the default) is at-the-money and
        preserves the previous behaviour exactly.

        This exists because "which strike" was silently held fixed at ATM
        across every measurement in this project until 2026-08-01, and it is
        not a neutral choice. A further-out strike is cheaper per lot, which
        is what makes it attractive on small capital, but the fixed Rs 20 per
        order is a far larger FRACTION of a small premium, and the bid-ask
        spread widens with distance. Whether that trade-off helps or hurts is
        a measurable question, and it could not even be asked while the
        parameter was hardcoded.

        Counting in STRIKES rather than rupees or percent is deliberate: the
        strike step differs by underlying (50 on NIFTY, 100 on BANKNIFTY),
        and stepping through the strikes that actually exist in the archive
        means the result cannot land on a strike nobody listed.
        """
        for expiry in self._expiries:
            if expiry < on:
                continue
            if (expiry - on).days > max_days_to_expiry:
                return None
            candidates = self._by_expiry.get((expiry, option_type))
            if not candidates:
                continue
            ordered = sorted(candidates, key=lambda c: c.strike)
            atm_index = min(
                range(len(ordered)), key=lambda i: (abs(ordered[i].strike - index_level), ordered[i].strike)
            )
            if strikes_out_of_the_money == 0:
                return ordered[atm_index]
            # Out-of-the-money is UP for a call and DOWN for a put.
            step = strikes_out_of_the_money if option_type == "CE" else -strikes_out_of_the_money
            target = atm_index + step
            # Refused, never clamped to the end of the chain: silently
            # returning the furthest available strike would report a result
            # for a distance the archive cannot actually support, and the
            # caller counts a `None` rather than mislabelling it.
            if not 0 <= target < len(ordered):
                return None
            return ordered[target]
        return None
