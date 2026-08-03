"""Reads `config/charges.yaml` and constructs a `ChargeRateTable`. Lives in
`te.data` (not `te.domain`) because it does file I/O — `te.domain` has none.
Date-based row selection itself (`select_rates`) is pure and lives in
`te.domain.costs`, unit-testable without touching a filesystem.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import Any

import yaml

from te.domain.costs import ChargeRates, ChargeRateTable
from te.domain.money import Paise


def _parse_row(raw: dict[str, Any]) -> ChargeRates:
    return ChargeRates(
        effective_from=raw["effective_from"],
        verified_at=raw["verified_at"],
        brokerage_per_executed_order_paise=Paise(int(raw["brokerage_per_executed_order_paise"])),
        stt_sell_bps=Decimal(str(raw["stt_sell_bps"])),
        stt_exercise_intrinsic_bps=Decimal(str(raw["stt_exercise_intrinsic_bps"])),
        exchange_txn_bps={exchange: Decimal(str(bps)) for exchange, bps in raw["exchange_txn_bps"].items()},
        sebi_bps=Decimal(str(raw["sebi_bps"])),
        gst_pct=Decimal(str(raw["gst_pct"])),
        stamp_buy_bps=Decimal(str(raw["stamp_buy_bps"])),
    )


def load_charge_rate_table(path: Path) -> ChargeRateTable:
    """Reads the full versioned rate table from `path` (typically
    `config/charges.yaml`). Row selection by trade date is
    `te.domain.costs.select_rates`, not this function."""
    with path.open("r", encoding="utf-8") as f:
        raw_rows = yaml.safe_load(f)
    return [_parse_row(row) for row in raw_rows]
