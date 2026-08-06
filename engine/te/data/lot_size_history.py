"""Reads `config/lot_sizes.yaml` — the HISTORICAL lot-size table used when
labelling or backtesting a date the broker can no longer be asked about.

Mirrors `charges_loader.py` deliberately: file I/O lives in `te.data`, and
the pure date-based selection is a small function here rather than in
`te.domain`, because unlike charge rates nothing in the domain layer needs
it (only backtest/label paths do).

### Live trading must never call this

`te.persistence.repos.instruments.latest_lot_size` (broker-synced, daily) is
the only lot size an order may be sized on — see the plan's R7, and
`te.engine.state.get_instrument_selections`, which forces an instrument
INACTIVE rather than trade it on an unconfirmed size. This module exists
only because a bar from 2024 has no broker to ask.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class LotSizeRow:
    effective_from: dt.date
    lot_size: int
    verified_at: dt.date
    note: str = ""


#: symbol -> rows, sorted oldest first.
LotSizeHistory = dict[str, list[LotSizeRow]]


class UnknownLotSizeError(LookupError):
    """Raised rather than falling back to a default.

    A wrong lot size does not fail loudly anywhere downstream — it quietly
    scales cost-per-unit, moving every cost-adjusted barrier by a few percent
    and changing win rates by an amount no output reveals. Refusing is the
    only failure mode that gets noticed."""


def load_lot_size_history(path: Path) -> LotSizeHistory:
    with path.open("r", encoding="utf-8") as f:
        raw: dict[str, Any] = yaml.safe_load(f)
    history: LotSizeHistory = {}
    for symbol, rows in (raw or {}).items():
        parsed = [
            LotSizeRow(
                effective_from=row["effective_from"],
                lot_size=int(row["lot_size"]),
                verified_at=row["verified_at"],
                note=str(row.get("note", "")).strip(),
            )
            for row in rows
        ]
        history[str(symbol).upper()] = sorted(parsed, key=lambda r: r.effective_from)
    return history


def lot_size_on(history: LotSizeHistory, symbol: str, on: dt.date) -> int:
    """The lot size in force for `symbol` on `on`.

    Raises `UnknownLotSizeError` when the symbol has no rows at all, or when
    `on` precedes the earliest row — never the oldest known size, which
    would be a guess wearing a number's clothes."""
    rows = history.get(symbol.upper())
    if not rows:
        raise UnknownLotSizeError(
            f"no historical lot size on file for {symbol!r} — add a VERIFIED row to "
            f"config/lot_sizes.yaml (see that file's header for why an invented one is worse)"
        )
    applicable = [row for row in rows if row.effective_from <= on]
    if not applicable:
        raise UnknownLotSizeError(
            f"{symbol} has no lot size on file for {on.isoformat()}; earliest row is "
            f"{rows[0].effective_from.isoformat()}"
        )
    return applicable[-1].lot_size
