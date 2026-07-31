"""`te.domain.signal` — `Direction`, `Signal`, `ExitPlan`. Pure value types;
`ExitPlan` is what makes it structurally impossible for `te.engine.exits` to
open a position without a real exit plan attached (see
`tests/engine/test_exits.py::test_every_open_position_has_an_exit_plan`)."""

from __future__ import annotations

import datetime as dt

import pytest

from te.domain.money import Paise
from te.domain.signal import ExitPlan, Signal


def _exit_plan(**overrides: object) -> ExitPlan:
    defaults: dict[str, object] = {
        "stop": Paise(1_800),
        "trailing_distance": Paise(200),
        # entry 2_000 + trail 200; engages at 2_000, above the 1_800 stop.
        "trailing_activation": Paise(2_200),
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
    plan = _exit_plan(trailing_distance=None, trailing_activation=None)
    assert plan.trailing_distance is None
    assert plan.trailing_activation is None


def test_exit_plan_rejects_a_trail_distance_without_an_activation_level() -> None:
    """The two fields are meaningless apart: a distance with no activation is
    the unconditional ratchet that silently overrode the stop-loss."""
    with pytest.raises(ValueError, match="set or unset together"):
        _exit_plan(trailing_distance=Paise(200), trailing_activation=None)


def test_exit_plan_rejects_target_not_above_stop() -> None:
    with pytest.raises(ValueError, match="target"):
        _exit_plan(stop=Paise(2_000), target=Paise(1_900))


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
