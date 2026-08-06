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
from decimal import Decimal
from typing import Literal

from te.domain.money import Paise

Direction = Literal["long_call", "long_put"]


@dataclass(frozen=True)
class ExitPlan:
    """Mandatory exit contract for one open position. Every field is
    required — there is no default that would let a caller construct an
    `ExitPlan` that silently omits one of stop/trailing/target/time-exit."""

    #: The premium the position was opened at. Held here, not just on
    #: `OpenPosition`, because `trailing_activation` is DERIVED from it —
    #: keeping them together is what makes the two impossible to disagree.
    #: `OpenPosition.entry_premium` reads through to this one value.
    entry_premium: Paise
    stop: Paise
    trailing_distance: Paise | None
    target: Paise
    max_hold: dt.timedelta
    hard_exit_by: dt.time
    #: Premium at which the ONE-TIME profit lock engages, and how far below
    #: THAT price (not entry) the locked stop sits — see
    #: `te.domain.geometry.ExitLevels.profit_lock_activation`'s docstring
    #: for the 2026-08-04 backtest that shaped these numbers. `None`/`None`
    #: means the rule is off; both are always set or unset together (see
    #: `__post_init__`), same convention as `trailing_distance`.
    profit_lock_activation: Paise | None = None
    profit_lock_buffer_pct: Decimal | None = None

    @property
    def trailing_activation(self) -> Paise | None:
        """Premium at which the trailing stop STARTS trailing. Below it the
        hard `stop` governs, unchanged.

        Always `entry + trailing_distance`, so the moment the trail first
        binds it sits exactly at break-even and can only improve from there.
        That is what makes it *protect* profit rather than quietly become a
        second, tighter entry stop.

        Without this gate, a trail narrower than the stop silently replaced
        it. Shipped defaults were a 20% stop and a 15% trail, so on the very
        first evaluation — at an unchanged price — `max(0.80*entry, entry -
        0.15*entry)` ratcheted the stop to 0.85*entry. The configured 20%
        stop was overwritten before it was ever tested, on every position;
        the real risk taken was 15% while `size_position` sized against 20%;
        and because `new_stop > plan.stop`, every exit was labelled
        `trailing_stop` — which is why 14 of 14 live trades on 2026-07-31
        reported that reason and none ever reported `stop` or `target`.

        A derived PROPERTY rather than a supplied field: it was briefly a
        field set by a helper at three call sites, which meant three places
        had to remember the definition and the rehydration path recomputed
        it separately. Deriving it makes drift unrepresentable instead of
        merely discouraged.
        """
        if self.trailing_distance is None:
            return None
        return Paise(self.entry_premium + self.trailing_distance)

    def __post_init__(self) -> None:
        if self.target <= self.stop:
            raise ValueError(f"target ({self.target}) must be above stop ({self.stop}) for a long option position")
        if not self.stop < self.entry_premium < self.target:
            raise ValueError(
                f"entry_premium ({self.entry_premium}) must sit between stop ({self.stop}) and target "
                f"({self.target}) — a long option position cannot open already past either barrier"
            )
        if self.max_hold <= dt.timedelta(0):
            raise ValueError(f"max_hold must be positive, got {self.max_hold!r}")
        if self.trailing_distance is not None and self.trailing_distance <= 0:
            raise ValueError(f"trailing_distance must be positive when set, got {self.trailing_distance!r}")
        if (self.profit_lock_activation is None) != (self.profit_lock_buffer_pct is None):
            raise ValueError("profit_lock_activation and profit_lock_buffer_pct must be set together")
        if self.profit_lock_activation is not None and not (self.stop < self.profit_lock_activation < self.target):
            raise ValueError(
                f"profit_lock_activation ({self.profit_lock_activation}) must sit between stop "
                f"({self.stop}) and target ({self.target})"
            )
        if self.profit_lock_buffer_pct is not None and not (0 < self.profit_lock_buffer_pct < 100):
            raise ValueError(f"profit_lock_buffer_pct must be in (0, 100), got {self.profit_lock_buffer_pct!r}")


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
