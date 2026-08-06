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
from te.engine.contract import UNDERLYING_INDEX_EXCHANGES
from te.persistence.db import make_engine, make_session_factory
from te.settings import Settings
from te.strategy.orb import OrbStrategy

#: Symbol -> the exchange its SPOT quote lives on, straight from
#: `te.engine.contract` rather than restated here — the engine already owns
#: this mapping, and a third copy is a third thing to update.
INSTRUMENTS: dict[str, str] = dict(UNDERLYING_INDEX_EXCHANGES)

#: Symbols backfilled for their DATA only — never replayed, never traded.
#:
#: Four of `te.ml.featurespec.SECONDARY_V1`'s eight columns are volatility
#: features (`india_vix_level`, `india_vix_term_slope`, `iv_rank_60d`,
#: `rv_iv_spread`) and `te.ml.dataset` reads them from `INDIAVIX` bars. With
#: no VIX in the store those four columns come out empty, and
#: `validate_training_set` SKIPS all-NaN columns rather than rejecting them —
#: so a model would train quietly on half a feature set and nobody would see
#: an error. Backfilled here so that cannot happen.
DATA_ONLY: dict[str, str] = {"INDIAVIX": "NSE_INDEX"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--start", required=True, help="YYYY-MM-DD")
    parser.add_argument("--end", required=True, help="YYYY-MM-DD")
    parser.add_argument("--backfill-only", action="store_true")
    parser.add_argument(
        "--strategy",
        default="orb",
        help=(
            "name the firings are RECORDED under. `orb` is the live engine's own name, so a "
            "replay run under it lands in the same bucket as real paper decisions AND as every "
            "earlier replay — including ones made under different rule parameters. "
            "`evaluation_id` is (name, instrument, minute) and is UNIQUE, so on any overlapping "
            "date the OLDER row wins and the new one is silently skipped, leaving one dataset "
            "holding two different strategies. Pass a distinct name for any parameter variant."
        ),
    )
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
        # Data-only symbols first — they feed features for every instrument,
        # so a partial run should not leave them missing.
        for symbol, exchange in {**DATA_ONLY, **instruments}.items():
            for window_start, window_end in month_windows(start, end):
                # BOTH intervals. 1m drives the ORB replay; DAILY drives the
                # model's volatility features — `iv_rank_60d`,
                # `india_vix_level` and `rv_iv_spread` all read
                # `interval="1d"`. Backfilling only 1m leaves those four
                # columns silently NaN, which is exactly what the first pass
                # did: 78,000 bars written, four of eight features dead, and
                # no error anywhere.
                for broker_interval in ("1m", "D"):
                    result = backfill_index_bars(
                        client, store, symbol=symbol, exchange=exchange,
                        start=window_start, end=window_end, interval=broker_interval,
                    )
                    total += result.bars_written
                    if not result.ok:
                        failures.append(f"{symbol} {broker_interval} {window_start}..{window_end}: {result.error}")
                    print(
                        f"  {symbol:10s} {broker_interval:>2s} {window_start}..{window_end}"
                        f"  {result.bars_written:>7,} bars"
                    )
        print(f"\nbackfill wrote {total:,} bars")
        for failure in failures:
            print(f"  FAILED {failure}", file=sys.stderr)

    if args.backfill_only:
        return 0

    engine = make_engine(settings.database_url)
    session_factory = make_session_factory(engine)
    result = replay_orb(
        store=store,
        session_factory=session_factory,
        instruments=instruments,
        start=start,
        end=end,
        strategy_name=args.strategy,
        # Load-bearing, not cosmetic: `strategy_name` alone only picks a
        # registry factory. The name actually RECORDED comes from the
        # strategy object, so without this the rows land under "orb"
        # regardless. Found the hard way on 2026-07-31 by a range-length
        # sweep whose every length wrote as "orb".
        strategy_factory=lambda: OrbStrategy(name=args.strategy),
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
