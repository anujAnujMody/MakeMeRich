"""`te.domain.geometry` — one representation of exit levels, two closed
variants, no way to mix them.

The bug this type exists to prevent: `CycleConfig` carried absolute distances
AND percentages simultaneously, resolved by `if ... is not None` at the use
site. Setting `trailing_pct=None` to DISABLE the trail fell through to the
leftover `trailing_distance=300`, restoring a Rs 3 absolute trail — 3.68% of
that day's Rs 81.50 NIFTY premium, and the same Rs 3 trail that had closed 14
of 14 trades on `trailing_stop` at a 3.1-minute average hold.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from te.domain.geometry import AbsolutePointGeometry, PremiumPercentGeometry, pct_of
from te.domain.money import Paise

ENTRY = Paise(8_150)  # Rs 81.50 — the real NIFTY entry from 2026-07-31


def test_disabling_the_trail_disables_it() -> None:
    """THE regression. In percentage mode there is no absolute value to fall
    back to, so `None` can only mean off."""
    levels = PremiumPercentGeometry(stop_pct=Decimal(20), target_pct=Decimal(20)).levels(ENTRY)

    assert levels.trailing_distance is None


def test_percentages_scale_with_the_premium() -> None:
    cheap = PremiumPercentGeometry(stop_pct=Decimal(20), target_pct=Decimal(20)).levels(Paise(3_000))
    dear = PremiumPercentGeometry(stop_pct=Decimal(20), target_pct=Decimal(20)).levels(Paise(30_000))

    assert cheap.stop == Paise(2_400)
    assert dear.stop == Paise(24_000)


def test_absolute_distances_do_not_scale() -> None:
    """The whole reason percentages exist: a fixed distance is a different
    strategy at a different premium."""
    geometry = AbsolutePointGeometry(stop_distance=Paise(300), target_distance=Paise(300))

    assert geometry.levels(Paise(3_000)).stop == Paise(2_700)  # -10%
    assert geometry.levels(Paise(30_000)).stop == Paise(29_700)  # -1%


def test_percent_helper_truncates() -> None:
    """The rounding is load-bearing and pinned by existing engine tests."""
    assert pct_of(Paise(9_655), Decimal(20)) == Paise(1_931)  # 1931.0 exactly
    assert pct_of(Paise(101), Decimal(50)) == Paise(50)  # 50.5 -> 50, not 51


@pytest.mark.parametrize(
    ("kwargs", "match"),
    [
        ({"stop_pct": Decimal(0), "target_pct": Decimal(40)}, "stop_pct"),
        ({"stop_pct": Decimal(100), "target_pct": Decimal(40)}, "stop_pct"),
        ({"stop_pct": Decimal(20), "target_pct": Decimal(0)}, "target_pct"),
        ({"stop_pct": Decimal(20), "target_pct": Decimal(40), "trailing_pct": Decimal(0)}, "trailing_pct"),
    ],
)
def test_percentage_geometry_rejects_nonsense(kwargs: dict[str, Decimal], match: str) -> None:
    with pytest.raises(ValueError, match=match):
        PremiumPercentGeometry(**kwargs)  # type: ignore[arg-type]


def test_a_geometry_cannot_hold_both_forms_at_once() -> None:
    """Structural, not a runtime check: the two variants have disjoint field
    names, so there is no object that carries a percentage AND an absolute
    distance for the same barrier."""
    percent_fields = set(PremiumPercentGeometry.__dataclass_fields__)
    absolute_fields = set(AbsolutePointGeometry.__dataclass_fields__)

    assert percent_fields & absolute_fields == set()
