import datetime as dt
from decimal import Decimal
from pathlib import Path

import pytest

from te.data.charges_loader import load_charge_rate_table
from te.domain.costs import select_rates

_YAML = """
- effective_from: 2026-04-01
  verified_at: 2026-07-29
  source: "https://zerodha.com/charges"
  brokerage_per_executed_order_paise: 2000
  stt_sell_bps: 15.0
  stt_exercise_intrinsic_bps: 15.0
  exchange_txn_bps:
    NFO: 3.553
    BFO: 3.25
  sebi_bps: 0.01
  gst_pct: 18.0
  stamp_buy_bps: 0.3
- effective_from: 2025-01-01
  verified_at: 2025-01-01
  source: "https://zerodha.com/charges"
  brokerage_per_executed_order_paise: 2000
  stt_sell_bps: 10.0
  stt_exercise_intrinsic_bps: 10.0
  exchange_txn_bps:
    NFO: 3.5
    BFO: 3.25
  sebi_bps: 0.01
  gst_pct: 18.0
  stamp_buy_bps: 0.3
"""


@pytest.fixture
def charges_path(tmp_path: Path) -> Path:
    path = tmp_path / "charges.yaml"
    path.write_text(_YAML, encoding="utf-8")
    return path


def test_loads_all_versioned_rows(charges_path: Path) -> None:
    table = load_charge_rate_table(charges_path)
    assert len(table) == 2
    effective_dates = {row.effective_from for row in table}
    assert effective_dates == {dt.date(2026, 4, 1), dt.date(2025, 1, 1)}


def test_row_fields_are_typed_correctly(charges_path: Path) -> None:
    table = load_charge_rate_table(charges_path)
    row = next(r for r in table if r.effective_from == dt.date(2026, 4, 1))
    assert row.brokerage_per_executed_order_paise == 2000
    assert row.stt_sell_bps == Decimal("15.0")
    assert row.exchange_txn_bps == {"NFO": Decimal("3.553"), "BFO": Decimal("3.25")}
    assert row.gst_pct == Decimal("18.0")


def test_loads_the_real_config_file() -> None:
    real_path = Path(__file__).resolve().parents[2] / "config" / "charges.yaml"
    table = load_charge_rate_table(real_path)
    assert len(table) >= 1
    # Sorted OLDEST first, and the table now spans four rate regimes back to
    # the start of the option archive (2024-01-04) — it held a single row
    # until 2026-08-01, when the earlier rates were researched and added.
    # Asserting a specific row INDEX would break on every future rate change;
    # what actually matters is that the table is ordered and that the current
    # regime is still the one being applied today.
    assert [r.effective_from for r in table] == sorted(r.effective_from for r in table)
    assert select_rates(table, dt.date(2026, 4, 1)).effective_from == dt.date(2026, 4, 1)
