"""`te.domain.signal` — `Direction`, `Signal`, `ExitPlan`. Pure value types;
`ExitPlan` is what makes it structurally impossible for `te.engine.exits` to
open a position without a real exit plan attached (see
`tests/engine/test_exits.py::test_every_open_position_has_an_exit_plan`)."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest

from te.domain.money import Paise
from te.domain.signal import ExitPlan, Signal


def _exit_plan(**overrides: object) -> ExitPlan:
    defaults: dict[str, object] = {
        "entry_premium": Paise(2_000),
        "stop": Paise(1_800),
        "trailing_distance": Paise(200),
        "target": Paise(2_400),
        "max_hold": dt.timedelta(hours=2),
        "hard_exit_by": dt.time(15, 20),
    }
    defaults.update(overrides)
    return ExitPlan(**defaults)  # type: ignore[arg-type]


def test_exit_plan_constructs_with_required_fields() -> None:
    plan = _exit_plan()
    assert plan.stop == Paise(1_800)
    assert plan.trailing_distance == Paise(200)
    assert plan.target == Paise(2_400)
    assert plan.max_hold == dt.timedelta(hours=2)
    assert plan.hard_exit_by == dt.time(15, 20)


def test_exit_plan_trailing_distance_is_optional() -> None:
    plan = _exit_plan(trailing_distance=None)
    assert plan.trailing_distance is None
    assert plan.trailing_activation is None


def test_trailing_activation_is_derived_from_entry_and_distance() -> None:
    """Activation is a PROPERTY, not a supplied field. It was briefly the
    latter, set by a helper at three call sites — so three places had to
    remember `entry + trailing_distance`, and the rehydration path computed
    it separately. Deriving it makes drift unrepresentable."""
    plan = _exit_plan(entry_premium=Paise(2_000), trailing_distance=Paise(200))

    assert plan.trailing_activation == Paise(2_200)
    # Engaging the trail can never tighten past the hard stop, by construction:
    # activation - distance is always exactly the entry price.
    assert plan.trailing_activation - Paise(200) == plan.entry_premium > plan.stop


def test_exit_plan_rejects_an_entry_outside_its_own_barriers() -> None:
    with pytest.raises(ValueError, match="must sit between stop"):
        _exit_plan(entry_premium=Paise(1_500))  # below the 1_800 stop


def test_exit_plan_rejects_an_entry_exactly_at_its_own_stop() -> None:
    """`stop < entry_premium < target` is a STRICT chain — an entry sitting
    exactly ON the stop is a position that opens already past its own
    barrier, per the module docstring's `__post_init__` note."""
    with pytest.raises(ValueError, match="must sit between stop"):
        _exit_plan(entry_premium=Paise(1_800))  # equals the stop, not above it


def test_exit_plan_rejects_an_entry_exactly_at_its_own_target() -> None:
    """The upper-bound twin of the above: an entry AT the target is already
    past the far barrier."""
    with pytest.raises(ValueError, match="must sit between stop"):
        _exit_plan(entry_premium=Paise(2_400))  # equals the target, not below it


def test_exit_plan_rejects_a_zero_max_hold() -> None:
    """`max_hold <= 0` must be refused — a zero hold time never lets a
    position exist at all."""
    with pytest.raises(ValueError, match="max_hold must be positive"):
        _exit_plan(max_hold=dt.timedelta(0))


def test_exit_plan_rejects_a_profit_lock_activation_exactly_at_the_stop() -> None:
    """`stop < profit_lock_activation < target` is strict, same reasoning as
    `entry_premium`'s own chain — a lock that activates exactly at the stop
    can never fire (the stop would already have exited the position)."""
    with pytest.raises(ValueError, match="must sit between stop"):
        _exit_plan(profit_lock_activation=Paise(1_800), profit_lock_buffer_pct=Decimal(5))  # equals the stop


def test_exit_plan_rejects_a_profit_lock_activation_exactly_at_the_target() -> None:
    """The upper-bound twin — an activation exactly AT the target can never
    fire either, since the target would already have closed the trade."""
    with pytest.raises(ValueError, match="must sit between stop"):
        _exit_plan(profit_lock_activation=Paise(2_400), profit_lock_buffer_pct=Decimal(5))  # equals the target


def test_exit_plan_rejects_target_not_above_stop() -> None:
    with pytest.raises(ValueError, match="target"):
        _exit_plan(stop=Paise(2_000), target=Paise(1_900))


def test_exit_plan_rejects_a_profit_lock_buffer_pct_of_exactly_zero() -> None:
    """`profit_lock_buffer_pct` must be in the OPEN interval `(0, 100)` — `0`
    itself must be refused, same lower-bound reasoning as
    `geometry.RupeeRiskGeometry`'s own equivalent check."""
    with pytest.raises(ValueError, match="profit_lock_buffer_pct must be in"):
        _exit_plan(profit_lock_activation=Paise(2_000), profit_lock_buffer_pct=Decimal(0))


def test_signal_construction() -> None:
    signal = Signal(
        strategy="orb",
        instrument="NIFTY30JUN2626500CE",
        direction="long_call",
        entry_premium=Paise(2_000),
        lot_size=65,
        ts=dt.datetime(2026, 7, 29, 9, 45, tzinfo=dt.UTC),
    )
    assert signal.direction == "long_call"
    assert signal.entry_premium == Paise(2_000)
