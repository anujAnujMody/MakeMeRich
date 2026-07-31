"""The trailing stop must protect profit, not silently replace the stop-loss.

Found live on 2026-07-31. Every one of the day's 14 closed trades reported
`exit_reason="trailing_stop"`; not one reported `stop` or `target`. That was
not a coincidence of the price paths — it was arithmetically forced.

With the shipped defaults (`stop_pct=20`, `trailing_pct=15`) a position opens
with `current_stop = 0.80 x entry`. On the FIRST exit evaluation, at an
unchanged price, the old unconditional ratchet computed
`max(0.80 x entry, entry - 0.15 x entry) = 0.85 x entry`. So:

* the configured 20% stop was overwritten before it was ever tested,
* the real risk taken was 15% while `size_position` sized against 20%,
* and `reason = "trailing_stop" if new_stop > plan.stop` was then true for
  every exit, destroying the diagnostic value of the field.

The fix gates the ratchet behind `ExitPlan.trailing_activation`
(`entry + trailing_distance`), so the trail first engages at break-even and
only ever locks in gains. These tests pin the consequence that matters: all
three price-based exits are reachable again.
"""

from __future__ import annotations

import datetime as dt

import pytest

from te.domain.money import Paise
from te.domain.signal import ExitPlan
from te.engine.exits import evaluate_position, open_position

ENTRY = Paise(67_630)  # Rs 676.30 — a real BANKNIFTY entry from that session
STOP = Paise(int(ENTRY * 0.80))  # -20%
TARGET = Paise(int(ENTRY * 1.40))  # +40%
TRAIL = Paise(int(ENTRY * 0.15))  # 15% of entry
OPENED_AT = dt.datetime(2026, 7, 31, 5, 0, tzinfo=dt.UTC)  # 10:30 IST, mid-session
NOW = OPENED_AT + dt.timedelta(minutes=1)


def _position() -> object:
    plan = ExitPlan(
        entry_premium=ENTRY,
        stop=STOP,
        trailing_distance=TRAIL,
        target=TARGET,
        max_hold=dt.timedelta(hours=3),
        hard_exit_by=dt.time(15, 20),
    )
    return open_position(
        symbol="BANKNIFTY25AUG2657200PE", exchange="NFO", strategy="orb", direction="long_put",
        lot_size=30, lots=1, opened_at=OPENED_AT, exit_plan=plan,
    )


def test_an_unmoved_position_does_not_tighten_its_stop() -> None:
    """The exact first-cycle case that broke it: price has not moved, so the
    trail must not engage and the stop must still be the configured 20%."""
    updated, decision = evaluate_position(_position(), current_premium=ENTRY, now=NOW)

    assert decision is None
    assert updated.current_stop == STOP, (
        f"stop moved to {updated.current_stop} on an unmoved position; the 20% stop was overwritten by the trail"
    )


def test_the_hard_stop_is_reachable_and_labelled_stop() -> None:
    """A trade that goes straight down must exit at -20% and SAY it was the
    stop — previously impossible, since the trail always sat above it."""
    _, decision = evaluate_position(_position(), current_premium=STOP, now=NOW)

    assert decision is not None
    assert decision.reason == "stop"


def test_the_target_is_reachable() -> None:
    _, decision = evaluate_position(_position(), current_premium=TARGET, now=NOW)

    assert decision is not None
    assert decision.reason == "target"


def test_the_trail_engages_only_once_in_profit_and_locks_in_break_even() -> None:
    """Below activation the stop is untouched; at activation the trail moves
    the stop to exactly break-even, never tighter than the hard stop."""
    position = _position()

    just_below = Paise(int(ENTRY + TRAIL) - 100)
    position, decision = evaluate_position(position, current_premium=just_below, now=NOW)
    assert decision is None
    assert position.current_stop == STOP, "the trail engaged before its activation level"

    at_activation = Paise(ENTRY + TRAIL)
    position, decision = evaluate_position(position, current_premium=at_activation, now=NOW)
    assert decision is None
    assert position.current_stop == ENTRY, (
        f"at activation the trail should sit at break-even ({ENTRY}), got {position.current_stop}"
    )


def test_a_winner_that_retreats_exits_in_profit_not_at_the_hard_stop() -> None:
    """The whole point of a trail: give back some of a large gain, but keep
    the rest. Runs to +35%, retreats — must exit above entry."""
    position = _position()
    high_water = Paise(int(ENTRY * 1.35))

    position, decision = evaluate_position(position, current_premium=high_water, now=NOW)
    assert decision is None
    assert position.current_stop == Paise(high_water - TRAIL)

    retreat = Paise(high_water - TRAIL)
    position, decision = evaluate_position(position, current_premium=retreat, now=NOW)

    assert decision is not None
    assert decision.reason == "trailing_stop"
    assert decision.exit_premium > ENTRY, "the trail exited at a loss despite the position having run to +35%"


def test_the_ratchet_never_loosens_when_price_falls_back() -> None:
    position = _position()
    position, _ = evaluate_position(position, current_premium=Paise(int(ENTRY * 1.35)), now=NOW)
    locked = position.current_stop

    # 1.25x is above the locked stop (1.35 - 0.15 = 1.20x), so the position
    # stays open — the point here is that the stop does not follow price DOWN.
    position, decision = evaluate_position(position, current_premium=Paise(int(ENTRY * 1.25)), now=NOW)

    assert decision is None
    assert position.current_stop == locked, "the trailing stop moved backward"


def test_the_trail_can_no_longer_be_built_below_the_hard_stop() -> None:
    """Activation is DERIVED (`entry + trailing_distance`), so a trail that
    engages below the hard stop is now unrepresentable rather than merely
    rejected — it would require an entry already at or under its own stop,
    which the constructor refuses outright."""
    plan = ExitPlan(
        entry_premium=ENTRY, stop=STOP, trailing_distance=TRAIL, target=TARGET,
        max_hold=dt.timedelta(hours=3), hard_exit_by=dt.time(15, 20),
    )
    assert plan.trailing_activation is not None
    assert plan.trailing_activation - TRAIL == ENTRY > STOP

    with pytest.raises(ValueError, match="must sit between stop"):
        ExitPlan(
            entry_premium=Paise(int(ENTRY * 0.70)),  # already below its own stop
            stop=STOP, trailing_distance=TRAIL, target=TARGET,
            max_hold=dt.timedelta(hours=3), hard_exit_by=dt.time(15, 20),
        )
