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


def test_premium_percent_target_is_added_not_subtracted() -> None:
    """`test_percentages_scale_with_the_premium` only ever checked `.stop`;
    nothing pinned the sign on `.target`, so a target computed by
    subtracting the percentage (putting it BELOW entry, on the wrong side of
    a long position) was indistinguishable from a correct one."""
    levels = PremiumPercentGeometry(stop_pct=Decimal(20), target_pct=Decimal(20)).levels(ENTRY)
    assert levels.target == Paise(9_780)  # 8150 + 20% of 8150 (1630, truncated)
    assert levels.target > ENTRY


def test_absolute_target_is_added_not_subtracted() -> None:
    """The `AbsolutePointGeometry` twin of the above — every existing test
    for this class only asserted `.stop`."""
    geometry = AbsolutePointGeometry(stop_distance=Paise(300), target_distance=Paise(500))
    levels = geometry.levels(Paise(3_000))
    assert levels.target == Paise(3_500)
    assert levels.target > Paise(3_000)


def test_absolute_stop_distance_of_zero_is_refused() -> None:
    """`__post_init__` must reject a zero stop distance, not just a negative
    one — a zero-distance stop sits exactly ON entry and can never define a
    real stop-loss."""
    with pytest.raises(ValueError, match="stop_distance must be positive"):
        AbsolutePointGeometry(stop_distance=Paise(0), target_distance=Paise(300))


def test_absolute_target_distance_of_zero_is_refused() -> None:
    with pytest.raises(ValueError, match="target_distance must be positive"):
        AbsolutePointGeometry(stop_distance=Paise(300), target_distance=Paise(0))


def test_absolute_stop_distance_exactly_at_the_entry_premium_is_degenerate() -> None:
    """The boundary the docstring's `>=` names: a stop distance EQUAL to the
    entry premium puts the stop at exactly zero, which is not a real price.
    `test_absolute_distances_do_not_scale` and friends never used a distance
    that reaches this exact boundary."""
    from te.domain.geometry import DegenerateGeometry

    geometry = AbsolutePointGeometry(stop_distance=Paise(3_000), target_distance=Paise(300))
    with pytest.raises(DegenerateGeometry):
        geometry.levels(Paise(3_000))
    # One paise short of the boundary works normally.
    levels = AbsolutePointGeometry(stop_distance=Paise(2_999), target_distance=Paise(300)).levels(Paise(3_000))
    assert levels.stop == Paise(1)


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
        (
            {"stop_pct": Decimal(20), "target_pct": Decimal(40), "profit_lock_activation_pct": Decimal(15)},
            "profit_lock_activation_pct and profit_lock_buffer_pct",
        ),
        (
            {"stop_pct": Decimal(20), "target_pct": Decimal(40), "profit_lock_buffer_pct": Decimal(5)},
            "profit_lock_activation_pct and profit_lock_buffer_pct",
        ),
        (
            {
                "stop_pct": Decimal(20),
                "target_pct": Decimal(40),
                "profit_lock_activation_pct": Decimal(0),
                "profit_lock_buffer_pct": Decimal(5),
            },
            "profit_lock_activation_pct",
        ),
        (
            {
                "stop_pct": Decimal(20),
                "target_pct": Decimal(40),
                "profit_lock_activation_pct": Decimal(15),
                "profit_lock_buffer_pct": Decimal(100),
            },
            "profit_lock_buffer_pct",
        ),
    ],
)
def test_percentage_geometry_rejects_nonsense(kwargs: dict[str, Decimal], match: str) -> None:
    with pytest.raises(ValueError, match=match):
        PremiumPercentGeometry(**kwargs)  # type: ignore[arg-type]


class TestProfitLock:
    """The ONE-TIME profit lock — distinct from the (disabled) continuous
    trail. Found live worth adding on 2026-08-04 after backtesting the exact
    shape against 1,305 real historical trades: roughly flat mean R but a
    materially higher win rate and no more full round-trips on a position
    that was already deep in profit."""

    def test_off_by_default(self) -> None:
        levels = PremiumPercentGeometry(stop_pct=Decimal(20), target_pct=Decimal(20)).levels(ENTRY)
        assert levels.profit_lock_activation is None
        assert levels.profit_lock_buffer_pct is None

    def test_activation_is_a_premium_above_entry(self) -> None:
        levels = PremiumPercentGeometry(
            stop_pct=Decimal(20),
            target_pct=Decimal(20),
            profit_lock_activation_pct=Decimal(15),
            profit_lock_buffer_pct=Decimal(5),
        ).levels(ENTRY)
        # 15% of 8150 = 1222 (truncated) -> 8150 + 1222 = 9372
        assert levels.profit_lock_activation == Paise(9_372)
        assert levels.profit_lock_buffer_pct == Decimal(5)

    def test_scales_with_the_premium_like_every_other_percentage_field(self) -> None:
        cheap = PremiumPercentGeometry(
            stop_pct=Decimal(20),
            target_pct=Decimal(20),
            profit_lock_activation_pct=Decimal(15),
            profit_lock_buffer_pct=Decimal(5),
        ).levels(Paise(3_000))
        dear = PremiumPercentGeometry(
            stop_pct=Decimal(20),
            target_pct=Decimal(20),
            profit_lock_activation_pct=Decimal(15),
            profit_lock_buffer_pct=Decimal(5),
        ).levels(Paise(30_000))
        assert cheap.profit_lock_activation == Paise(3_450)  # 3000 + 15%
        assert dear.profit_lock_activation == Paise(34_500)  # 30000 + 15%

    def test_absolute_geometry_never_carries_a_profit_lock(self) -> None:
        """Live-only feature — `AbsolutePointGeometry` backs backtests
        replayed from `option_bhav` and pre-percentage tests, never live
        trading, so it has no fields for this at all."""
        levels = AbsolutePointGeometry(stop_distance=Paise(300), target_distance=Paise(300)).levels(ENTRY)
        assert levels.profit_lock_activation is None
        assert levels.profit_lock_buffer_pct is None


def test_a_geometry_cannot_hold_both_forms_at_once() -> None:
    """Structural, not a runtime check: the two variants have disjoint field
    names, so there is no object that carries a percentage AND an absolute
    distance for the same barrier."""
    percent_fields = set(PremiumPercentGeometry.__dataclass_fields__)
    absolute_fields = set(AbsolutePointGeometry.__dataclass_fields__)

    assert percent_fields & absolute_fields == set()
