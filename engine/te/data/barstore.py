"""Parquet-backed bar store — the only place bars are physically written or
read from disk.

SQLite is transactional truth (orders, trades, decisions); Parquet is
immutable time series (bars, option chains) — see the plan's "Database"
section. Nothing in the API request path reads Parquet directly, and no
other module reads bars except through `te.data.asof.bars_asof`, which wraps
`BarStore.read`.

Partition layout on disk: ``{root}/interval={interval}/symbol={symbol}/
month={YYYY-MM}/part-*.parquet`` — one immutable part file per `append()`
call, never rewritten in place (append-only, safe for a single-writer
recorder process).
"""

from __future__ import annotations

import datetime as dt
import os
import re
import uuid
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

_MONTH_DIR_RE = re.compile(r"^month=(\d{4}-\d{2})$")

#: Every row appended to the store must carry all of these. `event_ts` is
#: the bar's OPEN time (what the bar refers to); `ingested_at` is when WE
#: recorded it — the two together are what makes `bars_asof()` leak-proof.
BAR_COLUMNS: tuple[str, ...] = (
    "symbol",
    "exchange",
    "event_ts",
    "interval",
    "o",
    "h",
    "l",
    "c",
    "v",
    "oi",
    "ingested_at",
    "source",
)

REQUIRED_COLUMNS = frozenset(BAR_COLUMNS)

#: Bounds for the implausible-EPOCH guard in `BarStore.append`.
#:
#: Deliberately wide. This guard exists to catch a timestamp that is not a
#: timestamp — principally a zero epoch — NOT to police whether a bar is
#: "too far in the future". (A millisecond value parsed as seconds lands
#: around the year 57,000, which pandas cannot represent at all; that one
#: raises at parse time and never reaches here.) The point-in-time gate in `te.data.asof` already
#: decides what a caller may SEE at a given `as_of`; making the store second-
#: guess that would duplicate the rule in two places and break every test
#: fixture that writes synthetic dates ahead of today.
#:
#: 2000-01-01 sits far below any NSE history this engine could legitimately
#: hold, and every observed failure lands decades below it.
_MIN_PLAUSIBLE_EVENT_TS = pd.Timestamp("2000-01-01", tz="UTC")
_MAX_PLAUSIBLE_EVENT_TS = pd.Timestamp("2100-01-01", tz="UTC")


def _to_utc_timestamp(value: dt.datetime) -> pd.Timestamp:
    ts = pd.Timestamp(value)
    return ts.tz_convert("UTC") if ts.tzinfo else ts.tz_localize("UTC")


class BarStore:
    """A partitioned Parquet store of OHLCV(+OI) bars rooted at `root`."""

    def __init__(self, root: Path | str) -> None:
        self._root = Path(root)

    @property
    def root(self) -> Path:
        return self._root

    def append(self, bars: pd.DataFrame) -> int:
        """Writes `bars` into the partitioned layout, one part file per
        (interval, symbol, month) group present in the input. Returns the
        number of rows written. Raises `ValueError` if any required column
        is missing. A no-op (returns 0) on an empty frame."""
        if bars.empty:
            return 0

        missing = REQUIRED_COLUMNS - set(bars.columns)
        if missing:
            raise ValueError(f"BarStore.append missing required columns: {sorted(missing)}")

        df = bars.copy()
        df["event_ts"] = pd.to_datetime(df["event_ts"], utc=True)
        df["ingested_at"] = pd.to_datetime(df["ingested_at"], utc=True)

        # Reject bars whose `event_ts` is not a plausible market timestamp.
        #
        # Found live on 2026-08-01: one NIFTY bar sat in the store stamped
        # 1970-01-01T00:29Z with entirely real-looking prices (o=24249.55,
        # h=24287.25), written by the live WS recorder on 2026-07-30. A tick
        # arriving with a zero/garbage epoch is silently believed, and the
        # result is a lone bar in a `month=1970-01` partition that no date
        # filter expects — which quietly widens the "first bar" of any range
        # scan by fifty-odd years.
        #
        # Rejected rather than clamped or repaired: there is no way to
        # recover the minute a broken timestamp meant, and inventing one puts
        # fabricated data in the one store this project treats as ground
        # truth. Raising (not dropping) is deliberate too — a silent drop
        # would hide a broken feed exactly as thoroughly as the bad bar did.
        #
        # NOT a "is this bar in the future" check — see the bounds' comment.
        bad = df.loc[(df["event_ts"] < _MIN_PLAUSIBLE_EVENT_TS) | (df["event_ts"] > _MAX_PLAUSIBLE_EVENT_TS)]
        if not bad.empty:
            sample = bad.iloc[0]
            raise ValueError(
                f"BarStore.append refused {len(bad)} bar(s) with an implausible event_ts — "
                f"first: symbol={sample['symbol']!r} event_ts={sample['event_ts']}. "
                "A tick with a garbage epoch must not enter the store."
            )

        df["_month"] = df["event_ts"].dt.strftime("%Y-%m")

        total = 0
        for (interval, symbol, month), group in df.groupby(["interval", "symbol", "_month"], sort=False):
            part_dir = self._root / f"interval={interval}" / f"symbol={symbol}" / f"month={month}"
            part_dir.mkdir(parents=True, exist_ok=True)
            # The timestamp is for human legibility only — it is NOT a
            # uniqueness guarantee. `datetime.now()` resolution is
            # platform-dependent (~15ms on Windows), so consecutive
            # `append()` calls routinely share a stamp; `total` only
            # disambiguates groups WITHIN one call. Without the random
            # suffix, two appends in the same clock tick resolve to the same
            # filename and `pq.write_table` silently overwrites the earlier
            # part — losing a whole bar with no error. That is precisely the
            # recorder's access pattern (one `append()` per closed bar).
            stamp = dt.datetime.now(dt.UTC).strftime("%Y%m%dT%H%M%S%f")
            part_path = part_dir / f"part-{stamp}-{total}-{uuid.uuid4().hex[:12]}.parquet"
            out = group.drop(columns=["_month"]).reset_index(drop=True)
            table = pa.Table.from_pandas(out[list(BAR_COLUMNS)], preserve_index=False)
            # Write to a sibling temp path, then atomically rename into place
            # (`os.replace` is atomic on both POSIX and Windows) — so a kill
            # mid-write (e.g. a container recreate's SIGKILL after the grace
            # period) can never leave a 0-byte/truncated `.parquet` file at
            # `part_path` for a reader to trip over. Found live on
            # 2026-07-30: `te.backtest.engine.run_backtest`'s first-ever run
            # against real recorded bars crashed on exactly this — three
            # 0-byte fragments from an earlier redeploy today, written
            # directly to their final path with no atomicity guarantee.
            tmp_path = part_path.with_suffix(".parquet.tmp")
            pq.write_table(table, tmp_path)
            os.replace(tmp_path, part_path)
            total += len(group)
        return total

    def read(
        self,
        symbol: str,
        start: dt.datetime,
        end: dt.datetime,
        interval: str,
        ingested_before: dt.datetime | None = None,
    ) -> pd.DataFrame:
        """Reads every part file under `interval={interval}/symbol={symbol}`
        whose `event_ts` falls in `[start, end]`, optionally filtering to
        rows `ingested_at <= ingested_before`. Returns rows sorted by
        `event_ts`. Empty (but correctly-columned) frame if nothing matches.

        Only the `month=` partitions that OVERLAP `[start, end]` are opened,
        and the range predicate is pushed down into Parquet rather than
        applied to a fully-materialised frame afterwards. This read runs
        once per instrument per cycle (via `te.data.asof.bars_asof`), so
        reading a symbol's entire recorded history to answer a one-hour
        lookback grows unboundedly with how long the recorder has run.

        Pruning is exact, not approximate: `month=` is derived from the
        UTC `event_ts` at write time (see `append`), so a row whose
        `event_ts` lies in `[start, end]` cannot physically live in a
        non-overlapping month partition. The `ingested_at` gate is
        deliberately NOT used for pruning — it is not the partition key —
        and is still applied in full below, so `bars_asof`'s point-in-time
        guarantee is unchanged."""
        symbol_dir = self._root / f"interval={interval}" / f"symbol={symbol}"
        if not symbol_dir.exists():
            return pd.DataFrame(columns=BAR_COLUMNS)

        start_utc = _to_utc_timestamp(start)
        end_utc = _to_utc_timestamp(end)

        parts = [
            part
            for month_dir in sorted(symbol_dir.glob("month=*"))
            if self._month_overlaps(month_dir.name, start_utc, end_utc)
            for part in sorted(month_dir.glob("part-*.parquet"))
        ]
        if not parts:
            return pd.DataFrame(columns=BAR_COLUMNS)

        filters: list[tuple[str, str, object]] = [
            ("event_ts", ">=", start_utc),
            ("event_ts", "<=", end_utc),
        ]
        ingested_before_utc = None if ingested_before is None else _to_utc_timestamp(ingested_before)
        if ingested_before_utc is not None:
            filters.append(("ingested_at", "<=", ingested_before_utc))

        frames = [pd.read_parquet(p, filters=filters) for p in parts]
        df = pd.concat(frames, ignore_index=True)
        if df.empty:
            return pd.DataFrame(columns=BAR_COLUMNS)

        df["event_ts"] = pd.to_datetime(df["event_ts"], utc=True)
        df["ingested_at"] = pd.to_datetime(df["ingested_at"], utc=True)

        # Re-applied in pandas over the (now small) pruned frame: the
        # predicate that decides what a strategy may see is asserted HERE,
        # never delegated to the storage engine's pushdown semantics.
        mask = (df["event_ts"] >= start_utc) & (df["event_ts"] <= end_utc)
        if ingested_before_utc is not None:
            mask &= df["ingested_at"] <= ingested_before_utc

        return df.loc[mask].sort_values("event_ts").reset_index(drop=True)

    @staticmethod
    def _month_overlaps(dir_name: str, start_utc: pd.Timestamp, end_utc: pd.Timestamp) -> bool:
        """`dir_name` is a `month=YYYY-MM` partition directory name.
        `YYYY-MM` strings sort lexicographically in chronological order, so
        an inclusive string comparison against the requested range's own
        months is exact. A directory whose name does not parse is kept
        (read it rather than risk silently dropping data)."""
        match = _MONTH_DIR_RE.match(dir_name)
        if match is None:
            return True
        month = match.group(1)
        return bool(start_utc.strftime("%Y-%m") <= month <= end_utc.strftime("%Y-%m"))
