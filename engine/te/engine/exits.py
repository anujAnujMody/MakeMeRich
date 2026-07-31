"""Manages exits on every open paper position, every cycle — the plan's
single most important file in this phase: "every position gets a real
stop-loss, trailing stop, and target/time exit; the user doesn't need to
know the terminology, the engine owns it end to end."

`OpenPosition` requires an `ExitPlan` as a constructor argument (not an
optional/settable-later field) — it is structurally impossible to construct
an open position without one; mypy --strict rejects any call site that
omits it, and there is no setter to attach one after the fact.

`next_trailing_stop()` is a pure function, unit-testable in isolation: as
premium moves favorably the stop ratchets up and NEVER moves backward. Both
`long_call` and `long_put` positions here are always LONG THE OPTION PREMIUM
(directional option BUYING only, per the plan — no writing/selling) — a
rising premium is favorable for either, so the ratchet direction is the same
for both; `direction` is threaded through anyway to keep the call site
explicit about what it's tracking and to leave room for a future short-
premium instrument without changing this function's signature.

`evaluate_position()` is the per-cycle decision: hard-exit-before-close and
max-hold fire regardless of stop/target/trail state (a position must never
be left open past session close no matter what its price path looks like);
otherwise target, then the (possibly just-ratcheted) stop, are checked in
that order.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, replace
from typing import Literal

from te.domain.clock import DEFAULT_SESSION, IST, SessionWindow
from te.domain.money import Paise
from te.domain.signal import Direction, ExitPlan

ExitReason = Literal["stop", "trailing_stop", "target", "time"]


@dataclass(frozen=True)
class OpenPosition:
    """A currently-open paper position. `exit_plan` is REQUIRED — there is
    no way to construct an `OpenPosition` without one (see the module
    docstring); `current_stop` is the live, ratcheted stop level, seeded
    from `exit_plan.stop` by `open_position()`."""

    symbol: str
    exchange: str
    strategy: str
    direction: Direction
    entry_premium: Paise
    lot_size: int
    lots: int
    opened_at: dt.datetime
    exit_plan: ExitPlan
    current_stop: Paise


@dataclass(frozen=True)
class ExitDecision:
    reason: ExitReason
    exit_premium: Paise


def open_position(
    *,
    symbol: str,
    exchange: str,
    strategy: str,
    direction: Direction,
    entry_premium: Paise,
    lot_size: int,
    lots: int,
    opened_at: dt.datetime,
    exit_plan: ExitPlan,
) -> OpenPosition:
    """The only way to construct an `OpenPosition` — `exit_plan` is a
    required keyword argument, so every open position leaves this function
    with a real stop/trailing/target/time-exit already attached."""
    return OpenPosition(
        symbol=symbol,
        exchange=exchange,
        strategy=strategy,
        direction=direction,
        entry_premium=entry_premium,
        lot_size=lot_size,
        lots=lots,
        opened_at=opened_at,
        exit_plan=exit_plan,
        current_stop=exit_plan.stop,
    )


def next_trailing_stop(
    current_stop: Paise, favorable_price: Paise, trail_distance: Paise, direction: Direction
) -> Paise:
    """Ratchets the trailing stop toward `favorable_price - trail_distance`,
    NEVER loosening it. `direction` doesn't change the arithmetic (see the
    module docstring — both directions here are long the premium) but is
    accepted for call-site clarity and future-proofing."""
    del direction  # see module docstring: identical math for both directions today
    candidate = Paise(favorable_price - trail_distance)
    return Paise(max(current_stop, candidate))


def time_exit(position: OpenPosition, *, now: dt.datetime, exit_premium: Paise) -> ExitDecision | None:
    """The two exits that depend only on the CLOCK, never on price.

    Split out of `evaluate_position` so the caller can still enforce them
    when the position cannot be priced. A quote outage must not be able to
    strand a position past `hard_exit_by` — but equally, a stop or target
    must never fire off a stale price, so the two kinds of exit need to be
    reachable independently. See `te.engine.cycle.run_exit_cycle`."""
    plan = position.exit_plan
    if now.astimezone(IST).timetz().replace(tzinfo=None) >= plan.hard_exit_by:
        return ExitDecision(reason="time", exit_premium=exit_premium)
    if now - position.opened_at >= plan.max_hold:
        return ExitDecision(reason="time", exit_premium=exit_premium)
    return None


def evaluate_position(
    position: OpenPosition,
    *,
    current_premium: Paise,
    now: dt.datetime,
    session: SessionWindow = DEFAULT_SESSION,
) -> tuple[OpenPosition, ExitDecision | None]:
    """Returns `(possibly-updated-position, decision)`. `decision` is
    `None` when the position stays open (with its trailing stop possibly
    ratcheted forward); otherwise it names which of stop / trailing_stop /
    target / time fired."""
    time_decision = time_exit(position, now=now, exit_premium=current_premium)
    if time_decision is not None:
        return position, time_decision

    plan = position.exit_plan

    if current_premium >= plan.target:
        return position, ExitDecision(reason="target", exit_premium=current_premium)

    # The trail only engages once the position has actually run far enough
    # to have profit worth protecting (`trailing_activation`, set to
    # `entry + trailing_distance`). Before that the hard stop governs
    # untouched — see `ExitPlan.trailing_activation` for the bug that made
    # this gate necessary: an unconditional ratchet let a 15% trail silently
    # replace a 20% stop on the first cycle of every position.
    new_stop = position.current_stop
    if plan.trailing_distance is not None and plan.trailing_activation is not None:
        if current_premium >= plan.trailing_activation:
            new_stop = next_trailing_stop(
                position.current_stop, current_premium, plan.trailing_distance, position.direction
            )
    updated = replace(position, current_stop=new_stop) if new_stop != position.current_stop else position

    if current_premium <= new_stop:
        reason: ExitReason = "trailing_stop" if new_stop > plan.stop else "stop"
        return updated, ExitDecision(reason=reason, exit_premium=current_premium)

    return updated, None
