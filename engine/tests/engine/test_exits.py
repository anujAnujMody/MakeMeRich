"""`te.engine.exits` — the plan's single most important file this phase:
every open position gets a real stop-loss, trailing stop, target, and
time-based exit, structurally, not by convention."""

from __future__ import annotations

import dataclasses
import datetime as dt

import pytest

from te.domain.clock import IST
from te.domain.money import Paise
from te.domain.signal import ExitPlan
from te.engine.exits import OpenPosition, evaluate_position, next_trailing_stop, open_position

OPENED_AT = dt.datetime(2026, 7, 29, 9, 45, tzinfo=IST)


def _plan(**overrides: object) -> ExitPlan:
    defaults: dict[str, object] = {
        "stop": Paise(1_800),
        "trailing_distance": Paise(200),
        # entry 2_000 + trail 200; engages at 2_000, above the 1_800 stop.
        "trailing_activation": Paise(2_200),
        "target": Paise(3_000),
        "max_hold": dt.timedelta(hours=3),
        "hard_exit_by": dt.time(15, 20),
    }
    defaults.update(overrides)
    return ExitPlan(**defaults)  # type: ignore[arg-type]


def _position(**overrides: object) -> OpenPosition:
    defaults: dict[str, object] = {
        "symbol": "NIFTY30JUN2626500CE",
        "exchange": "NFO",
        "strategy": "orb",
        "direction": "long_call",
        "entry_premium": Paise(2_000),
        "lot_size": 65,
        "lots": 1,
        "opened_at": OPENED_AT,
        "exit_plan": _plan(),
    }
    defaults.update(overrides)
    return open_position(**defaults)  # type: ignore[arg-type]


def test_every_open_position_has_an_exit_plan() -> None:
    """Attempting to construct an `OpenPosition` without an `ExitPlan` is a
    type/constructor error, not a runtime null-check."""
    with pytest.raises(TypeError):
        OpenPosition(  # type: ignore[call-arg]
            symbol="NIFTY30JUN2626500CE",
            exchange="NFO",
            strategy="orb",
            direction="long_call",
            entry_premium=Paise(2_000),
            lot_size=65,
            lots=1,
            opened_at=OPENED_AT,
            current_stop=Paise(1_800),
        )
    # open_position() (the only public constructor) also requires it —
    # omitting the keyword argument is a TypeError, not a silent default.
    kwargs = {
        "symbol": "NIFTY30JUN2626500CE",
        "exchange": "NFO",
        "strategy": "orb",
        "direction": "long_call",
        "entry_premium": Paise(2_000),
        "lot_size": 65,
        "lots": 1,
        "opened_at": OPENED_AT,
    }
    with pytest.raises(TypeError):
        open_position(**kwargs)  # type: ignore[call-arg, arg-type]


def test_open_position_seeds_current_stop_from_plan() -> None:
    position = _position()
    assert position.current_stop == position.exit_plan.stop


def test_trailing_stop_only_ratchets_forward() -> None:
    """Feed a sequence of favorable AND unfavorable price moves; the
    returned stop must be monotonically non-decreasing throughout, even
    across pullbacks."""
    stop = Paise(1_800)
    trail = Paise(200)
    price_path = [2_000, 2_400, 2_200, 2_600, 2_100, 3_000, 2_500]

    stops: list[Paise] = []
    for price in price_path:
        stop = next_trailing_stop(stop, Paise(price), trail, "long_call")
        stops.append(stop)

    assert stops == sorted(stops)  # monotonically non-decreasing
    for previous, current in zip(stops, stops[1:], strict=False):
        assert current >= previous


def test_trailing_stop_ratchets_forward_for_long_put_too() -> None:
    stop = Paise(1_800)
    trail = Paise(200)
    price_path = [2_000, 2_400, 2_200, 2_600]

    stops: list[Paise] = []
    for price in price_path:
        stop = next_trailing_stop(stop, Paise(price), trail, "long_put")
        stops.append(stop)

    for previous, current in zip(stops, stops[1:], strict=False):
        assert current >= previous


def test_position_stays_open_and_stop_ratchets_on_favorable_move() -> None:
    position = _position()
    now = OPENED_AT + dt.timedelta(minutes=10)
    updated, decision = evaluate_position(position, current_premium=Paise(2_500), now=now)

    assert decision is None
    assert updated.current_stop == Paise(2_500 - 200)  # ratcheted up
    assert updated.current_stop > position.exit_plan.stop


def test_trailing_stop_fires_after_pullback() -> None:
    position = _position()
    now = OPENED_AT + dt.timedelta(minutes=5)
    position, decision = evaluate_position(position, current_premium=Paise(2_500), now=now)
    assert decision is None
    assert position.current_stop == Paise(2_300)

    now = OPENED_AT + dt.timedelta(minutes=10)
    position, decision = evaluate_position(position, current_premium=Paise(2_250), now=now)

    assert decision is not None
    assert decision.reason == "trailing_stop"
    assert decision.exit_premium == Paise(2_250)


def test_hard_stop_fires_before_any_favorable_move() -> None:
    position = _position()
    now = OPENED_AT + dt.timedelta(minutes=5)
    _, decision = evaluate_position(position, current_premium=Paise(1_700), now=now)

    assert decision is not None
    assert decision.reason == "stop"


def test_target_fires() -> None:
    position = _position()
    now = OPENED_AT + dt.timedelta(minutes=5)
    _, decision = evaluate_position(position, current_premium=Paise(3_100), now=now)

    assert decision is not None
    assert decision.reason == "target"


def test_max_hold_time_exit_fires() -> None:
    position = _position()
    now = OPENED_AT + dt.timedelta(hours=4)  # past max_hold=3h
    _, decision = evaluate_position(position, current_premium=Paise(2_050), now=now)

    assert decision is not None
    assert decision.reason == "time"


def test_hard_exit_before_close_fires() -> None:
    """A position still open past the session's hard-exit time is closed
    regardless of stop/target/trail state — even mid-target, mid-favorable-
    trail, everything."""
    position = dataclasses.replace(_position(), current_stop=Paise(2_900))  # deep in profit, stop nearly at target
    now = dt.datetime(2026, 7, 29, 15, 25, tzinfo=IST)  # past hard_exit_by=15:20
    _, decision = evaluate_position(position, current_premium=Paise(2_950), now=now)

    assert decision is not None
    assert decision.reason == "time"


def test_no_trailing_distance_means_static_stop_only() -> None:
    position = _position(exit_plan=_plan(trailing_distance=None, trailing_activation=None))
    now = OPENED_AT + dt.timedelta(minutes=5)
    updated, decision = evaluate_position(position, current_premium=Paise(2_500), now=now)

    assert decision is None
    assert updated.current_stop == position.exit_plan.stop  # never ratcheted
