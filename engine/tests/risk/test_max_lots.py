"""`max_lots` — the ceiling that stops a rupee risk cap being multiplied.

Sizing picks a lot count by DIVISION:

    lots_by_risk = risk_budget // risk_per_lot

so shrinking the per-lot risk GROWS the position. That is fine while the
per-lot risk is a percentage of premium and moves with the market, and it is
a live defect the moment the owner sets an exact rupee stop: cap the per-lot
risk at Rs 700 against a Rs 2,500 budget and the engine buys three lots and
loses Rs 2,100. The setting reads as Rs 700 and delivers three times it.

`te.domain.geometry.RupeeRiskGeometry` and this ceiling are one feature in
two files. `test_a_rupee_stop_without_the_cap_is_multiplied` is the test that
says why — it asserts the BROKEN behaviour still exists when the cap is
absent, so nobody can conclude the geometry alone was sufficient.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from pathlib import Path

import pytest

from te.data.charges_loader import load_charge_rate_table
from te.domain.costs import CostModel, select_rates
from te.domain.money import Paise
from te.risk.sizing import size_position

_CHARGES = Path(__file__).resolve().parents[2] / "config" / "charges.yaml"
ON = dt.date(2026, 8, 6)

#: The real 2026-08-06 NIFTY trade, and the account it was taken on.
PREMIUM = Paise(16_680)
LOT_SIZE = 65
CAPITAL = Paise(50_000_00)
RISK_PCT = Decimal(5)  # Rs 2,500

#: Rs 700 spread over one lot of 65 = Rs 10.77 of premium.
RUPEE_STOP = Paise(int(PREMIUM) - 70_000 // LOT_SIZE)
FAR_TARGET = Paise(int(PREMIUM) + (70_000 // LOT_SIZE) * 10)


@pytest.fixture
def costs() -> CostModel:
    return CostModel(select_rates(load_charge_rate_table(_CHARGES), ON))


def _size(costs: CostModel, **overrides: object):  # noqa: ANN201
    kwargs: dict[str, object] = {
        "capital": CAPITAL,
        "risk_budget_pct": RISK_PCT,
        "premium": PREMIUM,
        "stop_premium": RUPEE_STOP,
        "target_premium": FAR_TARGET,
        "lot_size": LOT_SIZE,
        "costs": costs,
        "exchange": "NFO",
        "on": ON,
        "min_edge_multiple": Decimal("1.2"),
        "max_position_size_pct": Decimal(50),
    }
    return size_position(**{**kwargs, **overrides})  # type: ignore[arg-type]


def test_a_rupee_stop_without_the_cap_is_multiplied(costs: CostModel) -> None:
    """The defect, asserted so it cannot be quietly assumed away.

    With no ceiling the Rs 2,500 budget divided by Rs 700 of per-lot risk
    buys more than one lot, and the realised risk is a MULTIPLE of the cap
    the owner set. This is not a hypothetical failure mode — it is what
    shipping the geometry alone would have done.
    """
    result = _size(costs)
    assert result.lots > 1, "the premise of this test no longer holds — re-derive the numbers"
    assert int(result.risk_paise) > 70_000, "risk exceeded the Rs 700 cap, which is the point"


def test_the_cap_holds_the_loss_at_the_configured_rupees(costs: CostModel) -> None:
    """With the ceiling, the same inputs produce exactly the risk that was
    asked for."""
    result = _size(costs, max_lots=1)
    assert result.lots == 1
    assert int(result.risk_paise) <= 70_000
    # Within one lot-size of the cap: `Rs 700 // 65` truncates DOWN, so the
    # stop sits slightly nearer entry and the loss lands just under.
    assert int(result.risk_paise) >= 70_000 - LOT_SIZE


def test_the_cap_never_turns_a_tradeable_signal_into_a_rejection(costs: CostModel) -> None:
    """It is an owner-set ceiling, not a constraint discovered from the
    account, so it must only ever REDUCE a size that was already >= 1. If it
    could reject, its reason would surface as a sizing failure and blame the
    risk budget for a limit the risk budget never imposed."""
    uncapped = _size(costs)
    capped = _size(costs, max_lots=1)
    assert uncapped.lots >= 1
    assert capped.lots >= 1
    assert capped.rejected_reason is None


def test_the_cap_does_not_raise_a_size_it_only_lowers_it(costs: CostModel) -> None:
    """A ceiling far above what the account can afford must change nothing —
    otherwise `max_lots` would read as a target rather than a limit."""
    natural = _size(costs)
    generous = _size(costs, max_lots=1_000)
    assert generous.lots == natural.lots


def test_max_lots_below_one_is_refused(costs: CostModel) -> None:
    """`0` would mean "never trade", which is what the kill switch and the
    pause flag are for. Silently sizing to zero here would surface as a
    sizing rejection whose message names the wrong cause."""
    with pytest.raises(ValueError, match="max_lots must be at least 1"):
        _size(costs, max_lots=0)


def test_the_default_is_unchanged_behaviour(costs: CostModel) -> None:
    """Every pre-existing call site passes no `max_lots`, and must size
    exactly as it did before this parameter existed."""
    assert _size(costs).lots == _size(costs, max_lots=None).lots
