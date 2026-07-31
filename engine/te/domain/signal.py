"""`Direction`/`Signal`/`ExitPlan` — pure value types, no I/O.

`Signal` is what `te.strategy.orb` emits when every condition passes.
`ExitPlan` is the mandatory exit contract every open position must carry —
`te/engine/exits.py`'s `open_position()` requires one as a constructor
argument (not optional/settable-later), which is what makes it structurally
impossible to have an open position with no stop-loss/trailing-stop/target/
time-exit attached.

`te.domain` is pure (no I/O, no `te.*` imports) per the layer rule, so this
lives alongside `orders.py`/`events.py`, not in `te.strategy`, since both
`te.strategy.orb` and `te.engine.exits` need to construct/consume it and
`te.strategy` may never import `te.engine`.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Literal

from te.domain.money import Paise

Direction = Literal["long_call", "long_put"]


@dataclass(frozen=True)
class ExitPlan:
    """Mandatory exit contract for one open position. Every field is
    required — there is no default that would let a caller construct an
    `ExitPlan` that silently omits one of stop/trailing/target/time-exit."""

    stop: Paise
    trailing_distance: Paise | None
    #: Premium at which the trailing stop STARTS trailing. Below it the hard
    #: `stop` governs, unchanged.
    #:
    #: Without this, a trail narrower than the stop silently replaces it.
    #: Shipped defaults were a 20% stop and a 15% trail, so on the very first
    #: evaluation — at an unchanged price — `max(0.80*entry, entry - 0.15*
    #: entry)` ratcheted the stop to 0.85*entry. The configured 20% stop was
    #: overwritten before it was ever tested, on every position; the real
    #: risk taken was 15%, `size_position` sized against a 20% risk that
    #: never existed, and because `new_stop > plan.stop` every single exit
    #: was labelled `trailing_stop` — which is exactly why 14 of 14 live
    #: trades on 2026-07-31 reported that reason and none ever reported
    #: `stop` or `target`.
    #:
    #: Set to `entry + trailing_distance` by `te.engine.cycle`, so the trail
    #: first engages at break-even and from then on can only lock in gains —
    #: the standard meaning of a trailing stop, rather than a second, tighter
    #: entry stop wearing the name.
    trailing_activation: Paise | None
    target: Paise
    max_hold: dt.timedelta
    hard_exit_by: dt.time

    def __post_init__(self) -> None:
        if self.target <= self.stop:
            raise ValueError(f"target ({self.target}) must be above stop ({self.stop}) for a long option position")
        if self.max_hold <= dt.timedelta(0):
            raise ValueError(f"max_hold must be positive, got {self.max_hold!r}")
        if self.trailing_distance is not None and self.trailing_distance <= 0:
            raise ValueError(f"trailing_distance must be positive when set, got {self.trailing_distance!r}")
        if (self.trailing_distance is None) != (self.trailing_activation is None):
            raise ValueError(
                f"trailing_distance ({self.trailing_distance!r}) and trailing_activation "
                f"({self.trailing_activation!r}) must be set or unset together — a trail distance with no "
                f"activation level is the bug this field exists to prevent"
            )
        if self.trailing_activation is not None and self.trailing_distance is not None:
            engaged_at = self.trailing_activation - self.trailing_distance
            if engaged_at < self.stop:
                raise ValueError(
                    f"trailing_activation ({self.trailing_activation}) - trailing_distance "
                    f"({self.trailing_distance}) = {engaged_at}, which is BELOW the hard stop ({self.stop}). "
                    f"The trail would loosen the stop rather than tighten it."
                )


def trailing_activation_for(entry_premium: Paise, trailing_distance: Paise | None) -> Paise | None:
    """The premium at which a trail of `trailing_distance` should engage.

    Always `entry + trailing_distance`, so that the moment the trail first
    binds it sits exactly at break-even and can only improve from there. That
    single definition is what makes the trail *protect* profit instead of
    quietly becoming a tighter entry stop (see
    `ExitPlan.trailing_activation`).

    Kept as one function rather than repeated at each `ExitPlan` call site
    because it must agree with how a position is REHYDRATED from the
    database: `te.engine.cycle._row_to_position` recomputes activation from
    the stored `entry_premium_paise` + `trailing_distance_paise` rather than
    persisting a third column, so a second, drifting definition here would
    silently change a live position's stop the first time the engine
    restarted.
    """
    if trailing_distance is None:
        return None
    return Paise(entry_premium + trailing_distance)


@dataclass(frozen=True)
class Signal:
    """What `te.strategy.orb` (or any `Strategy`) emits when it decides to
    trade. Direction-only — sizing/exit-planning are `te.risk`/`te.engine`'s
    job, not the strategy's."""

    strategy: str
    instrument: str
    direction: Direction
    entry_premium: Paise
    lot_size: int
    ts: dt.datetime
