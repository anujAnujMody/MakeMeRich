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
