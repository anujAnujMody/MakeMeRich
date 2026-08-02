#!/usr/bin/env python
"""Loads the Shoonya historical option archive into the Parquet bar store.

Usage:
    python scripts/load_option_history.py --dry-run
    python scripts/load_option_history.py --instruments NIFTY --start 2026-04-01
    python scripts/load_option_history.py --instruments NIFTY,BANKNIFTY

### Why

Every ML label this project has produced so far was walked on the INDEX and
converted to a premium through ONE day's delta snapshot (`te.ml.barriers`),
with no theta and no IV dynamics. That makes every barrier outcome, every
win rate, and the whole meta-model training set optimistically biased — and
biased invisibly, because nothing in the numbers reveals it. Real 1-minute
option premiums remove the approximation entirely for the period they cover.

### Cost of a full run

The NIFTY archive is 670 MB compressed over 125 weekly expiries. Measured on
this machine: 3 expiries (768 contracts) took 66s and produced 3,997,450
bars, so a full NIFTY load is roughly 45 minutes and ~165 M bars. It is
resumable — a kill costs at most the contract in flight — so running it in
chunks with `--start`/`--end` is safe and re-running is a no-op.

### What this does NOT load

The `*_spot.csv` index series in each inner zip is skipped. The store already
holds live-recorded index bars under those keys; interleaving two sources
for one symbol leaves no way to tell them apart afterwards.
"""

from __future__ import annotations

import argparse
import datetime as dt
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from te.data.barstore import BarStore  # noqa: E402
from te.data.option_history import default_manifest_path, load_option_history  # noqa: E402
from te.settings import Settings  # noqa: E402

logger = logging.getLogger("load_option_history")

#: One archive per underlying, named as downloaded. BANKNIFTY holds only 4
#: expiries (2026-01-27 .. 2026-04-28) against NIFTY's 125 — stated here
#: rather than discovered at the end of a long run.
ARCHIVES = {
    "NIFTY": "nifty_all.zip",
    "BANKNIFTY": "banknifty_all.zip",
}

DEFAULT_ARCHIVE_DIR = Path(__file__).resolve().parents[2] / "data" / "raw" / "shoonya"


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--instruments", default="NIFTY", help=f"comma-separated, from {','.join(ARCHIVES)}")
    parser.add_argument("--archive-dir", type=Path, default=DEFAULT_ARCHIVE_DIR)
    parser.add_argument("--start", type=dt.date.fromisoformat, default=None, help="earliest EXPIRY date, YYYY-MM-DD")
    parser.add_argument("--end", type=dt.date.fromisoformat, default=None, help="latest EXPIRY date, YYYY-MM-DD")
    parser.add_argument("--dry-run", action="store_true", help="parse and count, write nothing")
    parser.add_argument(
        "--spot-cutoff",
        type=dt.date.fromisoformat,
        default=None,
        help=(
            "also load the archive's INDEX series, for bars strictly BEFORE this date. "
            "Omit to skip the index entirely (the default, and what an incremental option-only "
            "top-up wants). Set it to the first date this engine recorded its own index bars, so "
            "the two sources cannot overlap under the same symbol key — see te.data.option_history."
        ),
    )
    args = parser.parse_args()

    chosen = [s.strip().upper() for s in args.instruments.split(",") if s.strip()]
    unknown = [s for s in chosen if s not in ARCHIVES]
    if unknown:
        print(f"no archive known for: {', '.join(unknown)}", file=sys.stderr)
        return 2
    if args.start is not None and args.end is not None and args.end < args.start:
        parser.error("--end must not be before --start")

    store = BarStore(Settings().bar_store_path)
    print(f"bar store: {store.root}")
    print(f"manifest:  {default_manifest_path(store)}")
    if args.dry_run:
        print("DRY RUN — nothing will be written\n")

    exit_code = 0
    for instrument in chosen:
        archive = args.archive_dir / ARCHIVES[instrument]
        if not archive.exists():
            print(f"{instrument:<11} archive missing: {archive}", file=sys.stderr)
            exit_code = 1
            continue

        result = load_option_history(
            archive,
            store,
            start=args.start,
            end=args.end,
            dry_run=args.dry_run,
            spot_cutoff=args.spot_cutoff,
            progress=lambda message, i=instrument: logger.info("%s %s", i, message),
        )
        # Skips are reported next to loads, always — a run that wrote nothing
        # because everything was already loaded must not read as a success
        # that produced 0 bars.
        print(
            f"{instrument:<11} expiries loaded={result.expiries_loaded} skipped={result.expiries_skipped}  "
            f"contracts loaded={result.contracts_loaded} already-loaded={result.contracts_skipped}  "
            f"bars written={result.bars_written:,}"
        )

    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
