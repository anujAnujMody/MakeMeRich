#!/usr/bin/env python
"""CLI to backfill historical index bars and replay the ORB rule over them.

Usage:
    # Fetch history only (safe to re-run; see the idempotency note below)
    python scripts/replay_orb_history.py --start 2025-09-01 --end 2026-07-30 --backfill-only

    # Replay only, over bars already on disk
    python scripts/replay_orb_history.py --start 2025-09-01 --end 2026-07-30 --replay-only

    # Both, the normal case
    python scripts/replay_orb_history.py --start 2025-09-01 --end 2026-07-30

Why 2025-09-01 is the sensible floor: NSE moved all index derivatives to a
Tuesday expiry on 2025-09-01, and BANKNIFTY weeklies were discontinued in
November 2024. Bars before that describe a market with a different expiry
structure, so firings replayed over them are not samples of the market the
engine trades today.

**Backfill is NOT idempotent in the store.** `BarStore.append` writes a new
part file per run; re-running the same window duplicates rows on disk.
`te.strategy.orb` de-duplicates on `event_ts` before its edge comparison, so
a duplicate does not corrupt a signal, but the store grows. Prefer
`--replay-only` when the bars are already down.

Requires the same `TE_*` settings as the running engine.
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys

from te.backtest.replay import replay_orb
from te.broker.openalgo_rest import OpenAlgoRestClient
from te.data.barstore import BarStore
from te.data.history_backfill import backfill_index_bars, month_windows
from te.persistence.db import make_engine, make_session_factory
from te.settings import Settings

#: Symbol -> the exchange its SPOT quote lives on. Options are placed on
#: NFO/BFO, but the ORB rule reads the INDEX, and an index quotes on
#: `NSE_INDEX`/`BSE_INDEX` — the same split `te.engine.contract` handles.
INSTRUMENTS: dict[str, str] = {
    "NIFTY": "NSE_INDEX",
    "BANKNIFTY": "NSE_INDEX",
    "SENSEX": "BSE_INDEX",
    "BANKEX": "BSE_INDEX",
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--start", required=True, help="YYYY-MM-DD")
    parser.add_argument("--end", required=True, help="YYYY-MM-DD")
    parser.add_argument("--backfill-only", action="store_true")
    parser.add_argument("--replay-only", action="store_true")
    parser.add_argument(
        "--instruments",
        default=",".join(INSTRUMENTS),
        help="comma-separated subset of " + ",".join(INSTRUMENTS),
    )
    args = parser.parse_args()

    start = dt.date.fromisoformat(args.start)
    end = dt.date.fromisoformat(args.end)
    if start > end:
        print(f"--start {start} is after --end {end}", file=sys.stderr)
        return 2

    chosen = [s.strip().upper() for s in args.instruments.split(",") if s.strip()]
    unknown = [s for s in chosen if s not in INSTRUMENTS]
    if unknown:
        print(f"unknown instrument(s): {', '.join(unknown)}", file=sys.stderr)
        return 2
    instruments = {s: INSTRUMENTS[s] for s in chosen}

    settings = Settings()
    store = BarStore(settings.bar_store_path)

    if not args.replay_only:
        client = OpenAlgoRestClient(settings.openalgo_host, settings.openalgo_api_key.get_secret_value(), timeout=60.0)
        total = 0
        failures: list[str] = []
        for symbol, exchange in instruments.items():
            for window_start, window_end in month_windows(start, end):
                result = backfill_index_bars(
                    client, store, symbol=symbol, exchange=exchange, start=window_start, end=window_end
                )
                total += result.bars_written
                if not result.ok:
                    failures.append(f"{symbol} {window_start}..{window_end}: {result.error}")
                print(f"  {symbol:10s} {window_start}..{window_end}  {result.bars_written:>7,} bars")
        print(f"\nbackfill wrote {total:,} bars")
        for failure in failures:
            print(f"  FAILED {failure}", file=sys.stderr)

    if args.backfill_only:
        return 0

    engine = make_engine(settings.database_url)
    session_factory = make_session_factory(engine)
    result = replay_orb(
        store=store, session_factory=session_factory, instruments=instruments, start=start, end=end
    )

    print(f"\nreplay over {result.days} instrument-days")
    print(f"  evaluations {result.evaluations:,}")
    print(f"  FIRINGS     {result.firings:,}")
    for symbol, count in sorted(result.firings_by_instrument.items()):
        print(f"    {symbol:10s} {count:,}")
    print(f"  skips       {result.skips:,}")
    for reason, count in sorted(result.skip_reasons.items(), key=lambda kv: -kv[1])[:8]:
        print(f"    {count:>7,}  {reason[:80]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
