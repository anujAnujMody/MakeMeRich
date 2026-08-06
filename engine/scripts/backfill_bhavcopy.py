#!/usr/bin/env python
"""CLI to backfill historical NSE/BSE F&O bhavcopy days on demand.

Usage:
    python scripts/backfill_bhavcopy.py --start 2025-09-01 --end 2025-09-30
    python scripts/backfill_bhavcopy.py --start 2026-07-01 --end 2026-07-29 --exchange nse

Iterates every calendar day in `[start, end]`, skips weekends, and calls
`ingest_bhavcopy_nse`/`ingest_bhavcopy_bse` per day — each call fetches,
parses, and writes a `bar_ingest_log` row (success or failed) via
`te.data.ingest_log.record_ingest`. Designed to be re-run safely: a day
that already succeeded still re-fetches (bhavcopy files don't change after
publication) and appends another log row, which is intentional — the log is
an append-only audit trail, not a completion flag.

Requires `TE_*` settings in the environment (same as the running engine) so
it writes ingest-log rows into the right (dev/prod) database — see
`te.settings.Settings`.
"""

from __future__ import annotations

import argparse
import datetime as dt
import logging
import sys
from collections.abc import Iterator
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from te.data.bhavcopy_bse import ingest_bhavcopy_bse  # noqa: E402
from te.data.bhavcopy_nse import ingest_bhavcopy_nse  # noqa: E402
from te.persistence.db import engine_from_settings  # noqa: E402
from te.settings import Settings  # noqa: E402

logger = logging.getLogger("backfill_bhavcopy")


def _daterange(start: dt.date, end: dt.date) -> Iterator[dt.date]:
    current = start
    one_day = dt.timedelta(days=1)
    while current <= end:
        yield current
        current += one_day


def backfill(start: dt.date, end: dt.date, exchange: str) -> None:
    settings = Settings()
    engine = engine_from_settings(settings)

    for trade_date in _daterange(start, end):
        if trade_date.weekday() >= 5:  # Saturday/Sunday — no bhavcopy published
            continue

        if exchange in ("nse", "both"):
            try:
                rows = ingest_bhavcopy_nse(engine, trade_date)
                logger.info("NSE %s: %d rows", trade_date, len(rows))
            except Exception as exc:  # noqa: BLE001 — CLI: log and keep going
                logger.error("NSE %s failed: %s", trade_date, exc)

        if exchange in ("bse", "both"):
            try:
                rows = ingest_bhavcopy_bse(engine, trade_date)
                logger.info("BSE %s: %d rows", trade_date, len(rows))
            except Exception as exc:  # noqa: BLE001
                logger.error("BSE %s failed: %s", trade_date, exc)

    engine.dispose()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", required=True, type=dt.date.fromisoformat, help="YYYY-MM-DD")
    parser.add_argument("--end", required=True, type=dt.date.fromisoformat, help="YYYY-MM-DD")
    parser.add_argument("--exchange", choices=["nse", "bse", "both"], default="both")
    args = parser.parse_args()

    if args.end < args.start:
        parser.error("--end must not be before --start")

    backfill(args.start, args.end, args.exchange)


if __name__ == "__main__":
    main()
