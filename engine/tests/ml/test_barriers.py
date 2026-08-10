"""`te.ml.barriers` — the option-premium to index-point conversions every
labelled result rests on.

These were previously constants and helpers inside
`scripts/label_replay_firings.py`, imported by four sibling scripts, so the
project's single most load-bearing unit conversion had no test at all
(`testpaths` is `tests/`). Getting it wrong is not theoretical: costing a
round trip on the index LEVEL as though it were an option premium inflated a
73.9-point target to 179.1 and helped produce an 8.7% apparent win rate
against a ~33% random-walk baseline.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from pathlib import Path

import pytest

from te.data.charges_loader import load_charge_rate_table
from te.domain.costs import CostModel, select_rates
from te.domain.geometry import PremiumPercentGeometry, RupeeRiskGeometry
from te.domain.money import Paise
from te.ml.barriers import (
    ATM_SNAPSHOTS,
    barrier_pct_as_index_pct,
    barriers,
    index_barriers,
    round_trip_cost_in_index_points,
)

_CHARGES_PATH = Path(__file__).resolve().parents[2] / "config" / "charges.yaml"
ON = dt.date(2026, 7, 31)


@pytest.fixture
def cost_model() -> CostModel:
    return CostModel(select_rates(load_charge_rate_table(_CHARGES_PATH), ON))


def test_barriers_convert_through_delta() -> None:
    """A 20% move on a Rs 92.05 premium at delta 0.4982 is 36.95 index
    points — the option moves `delta` per index point, so the index has to
    move `premium_change / delta`."""
    stop, target = index_barriers("NIFTY", stop_pct=Decimal(20), target_pct=Decimal(40))

    assert int(stop) == pytest.approx(3_695, abs=2)  # 36.95 points, in paise
    assert int(target) == pytest.approx(7_390, abs=2)  # 73.90 points
    assert int(target) == pytest.approx(2 * int(stop), abs=2)  # ratio preserved


def test_the_same_premium_percentage_is_a_different_index_move_per_underlying() -> None:
    """THE finding this module exists to make legible. BANKNIFTY has no
    weekly expiry, so its ATM is 25 DTE with far lower gamma: +40% on the
    premium needs a ~1.1% index move against NIFTY's ~0.30%. One shared
    barrier would describe four different strategies as one."""
    nifty = barrier_pct_as_index_pct("NIFTY", Decimal(40))
    banknifty = barrier_pct_as_index_pct("BANKNIFTY", Decimal(40))

    assert nifty == pytest.approx(0.303, abs=0.01)
    assert banknifty == pytest.approx(1.114, abs=0.01)
    assert banknifty > 3 * nifty


def test_cost_in_index_points_is_small_not_index_scaled(cost_model: CostModel) -> None:
    """The bug: costing a round trip on the index LEVEL (~24,400) as though
    it were an option premium returned Rs 105.18 per unit, which inflated a
    73.9-point target to 179.1 — 2.42x — while leaving the stop untouched.

    Priced on the real option notional it is a couple of index points."""
    cost = round_trip_cost_in_index_points("NIFTY", cost_model, ON)

    assert 0 < int(cost) < 1_000, f"{int(cost)}p is not a plausible index-point cost"
    stop, _ = index_barriers("NIFTY", stop_pct=Decimal(20), target_pct=Decimal(40))
    assert int(cost) < int(stop) / 5, "cost should be a small fraction of the stop distance"


def test_barriers_with_no_geometry_raises_rather_than_falling_back_to_20_20() -> None:
    """The old bug: a caller that forgot to pass a geometry used to get a
    silent 20%/20% label describing a strategy nothing traded. It must now
    raise loudly instead."""
    with pytest.raises(ValueError, match="ExitGeometry"):
        barriers("NIFTY")


def test_nifty_barriers_under_the_live_rupee_risk_geometry_are_not_1_to_1() -> None:
    """The bug this task fixes. `Settings.paper_cycle_max_loss_per_trade_paise`
    (Rs 700) and `paper_cycle_target_risk_multiple` (10) are what the live
    engine actually trades under `RupeeRiskGeometry` — a roughly 1:10
    risk:reward, not the hardcoded 20%/20% (1:1) `barriers()` used to always
    return regardless of geometry."""
    geometry = RupeeRiskGeometry(max_loss_paise=Paise(70_000), target_multiple=Decimal(10))

    stop, target = barriers("NIFTY", geometry=geometry, max_lots=1)

    # The old, wrong behaviour: 20% stop / 20% target of NIFTY's premium.
    old_stop, old_target = index_barriers("NIFTY", stop_pct=Decimal(20), target_pct=Decimal(20))
    assert (int(stop), int(target)) != (int(old_stop), int(old_target))
    # Not 1:1 — the target must be a large multiple of the stop, not equal to it.
    assert int(target) > 5 * int(stop), f"stop={int(stop)}p target={int(target)}p is not far from 1:1"


def test_barriers_under_rupee_risk_geometry_matches_a_hand_derived_percentage() -> None:
    """Re-derives the expected NIFTY stop/target percentage independently
    from `ATM_SNAPSHOTS` and `RupeeRiskGeometry.levels()` — the same
    computation the brief specifies — and checks `barriers()` restates it
    through the SAME `index_barriers` conversion as every other caller."""
    geometry = RupeeRiskGeometry(max_loss_paise=Paise(70_000), target_multiple=Decimal(10))
    snapshot = ATM_SNAPSHOTS["NIFTY"]
    entry_premium = Paise(round(snapshot.premium_rupees * 100))
    quantity = snapshot.lot_size * 1
    levels = geometry.levels(entry_premium, quantity=quantity)
    expected_stop_pct = Decimal(int(entry_premium) - int(levels.stop)) / Decimal(int(entry_premium)) * 100
    expected_target_pct = Decimal(int(levels.target) - int(entry_premium)) / Decimal(int(entry_premium)) * 100
    expected = index_barriers("NIFTY", stop_pct=expected_stop_pct, target_pct=expected_target_pct)

    assert barriers("NIFTY", geometry=geometry, max_lots=1) == expected


def test_barriers_are_symbol_specific_under_a_rupee_cap() -> None:
    """Under a rupee cap the same Rs 700 is a different percentage of every
    symbol's premium (NIFTY Rs 92.05 vs BANKNIFTY Rs 876.75), so the derived
    barriers must differ per symbol rather than sharing one percentage."""
    geometry = RupeeRiskGeometry(max_loss_paise=Paise(70_000), target_multiple=Decimal(10))

    nifty_stop, _ = barriers("NIFTY", geometry=geometry, max_lots=1)
    banknifty_stop, _ = barriers("BANKNIFTY", geometry=geometry, max_lots=1)

    assert int(nifty_stop) != int(banknifty_stop)


def test_barriers_still_work_with_a_premium_percent_geometry() -> None:
    """`PremiumPercentGeometry` ignores `quantity`, so `barriers()` must
    still reproduce the plain percentage conversion for the geometry variant
    that IS a fixed percentage — this is a regression check that the
    geometry-derived path agrees with `index_barriers` when the percentage
    really is fixed."""
    geometry = PremiumPercentGeometry(stop_pct=Decimal(20), target_pct=Decimal(40))

    stop, target = barriers("NIFTY", geometry=geometry, max_lots=1)
    expected = index_barriers("NIFTY", stop_pct=Decimal(20), target_pct=Decimal(40))

    assert (int(stop), int(target)) == pytest.approx((int(expected[0]), int(expected[1])), abs=2)


@pytest.mark.parametrize("symbol", sorted(ATM_SNAPSHOTS))
def test_every_snapshot_is_internally_consistent(symbol: str) -> None:
    """Guards against a typo in a hand-entered measured value: an ATM option
    has delta near 0.5, a premium well below its spot, and a real lot size."""
    snapshot = ATM_SNAPSHOTS[symbol]

    assert 0.40 < snapshot.delta < 0.60, f"{symbol} delta {snapshot.delta} is not ATM-like"
    assert 0 < snapshot.premium_rupees < snapshot.spot * 0.05
    assert snapshot.lot_size > 0
    assert snapshot.exchange in {"NFO", "BFO"}
