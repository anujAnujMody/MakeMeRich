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
from decimal import Decimal
from typing import Literal

from te.domain.clock import DEFAULT_SESSION, IST, SessionWindow
from te.domain.geometry import pct_of
from te.domain.money import Paise
from te.domain.signal import Direction, ExitPlan

ExitReason = Literal["stop", "trailing_stop", "target", "time", "profit_lock"]


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
    lot_size: int
    lots: int
    opened_at: dt.datetime
    exit_plan: ExitPlan
    current_stop: Paise
    #: Whether the one-time profit lock (see `ExitPlan.profit_lock_activation`)
    #: has already fired. `False` until `evaluate_position` engages it, then
    #: permanently `True` for the rest of the position's life — it is a
    #: ONE-TIME jump, not a continuous ratchet like trailing.
    profit_lock_engaged: bool = False

    @property
    def entry_premium(self) -> Paise:
        """Read through to the plan — ONE stored entry price. Holding it in
        both places would let a position and its own exit levels disagree
        about what was paid."""
        return self.exit_plan.entry_premium


@dataclass(frozen=True)
class ExitDecision:
    reason: ExitReason
    exit_premium: Paise


def _next_profit_lock_rung(plan: ExitPlan, position: OpenPosition) -> Paise:
    """The premium at which the profit lock next steps up.

    DERIVED rather than stored, which is what lets the one-time lock become
    a ratchet without a schema change or a new persisted column:

    * before the first rung, it is simply `plan.profit_lock_activation`
    * after a rung has fired, the price that fired it is recoverable from
      the stop it set (`stop = price x (1 - buffer%)`), and the next rung is
      that price grown by the same step ratio the first rung used
      (`activation / entry`, e.g. 1.15 for a +15% step)

    Both inputs are already on the plan, so the sequence of rungs is a pure
    function of stored state and cannot drift from it.

    If a continuous TRAIL is also running it may have raised `current_stop`
    above the last locked level. The derived rung is then higher than a
    stored one would be, so the lock fires LESS often — the safe direction,
    and never a stop that moves down. In practice this project runs at most
    one of the two mechanisms at a time.
    """
    activation, buffer = plan.profit_lock_activation, plan.profit_lock_buffer_pct
    if activation is None or buffer is None:
        raise ValueError("no profit lock configured — callers must check before asking for a rung")
    if not position.profit_lock_engaged:
        return activation
    last_locked_at = Decimal(int(position.current_stop)) * Decimal(100) / (Decimal(100) - buffer)
    step_ratio = Decimal(int(activation)) / Decimal(int(plan.entry_premium))
    return Paise(int(last_locked_at * step_ratio))


def open_position(
    *,
    symbol: str,
    exchange: str,
    strategy: str,
    direction: Direction,
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
        lot_size=lot_size,
        lots=lots,
        opened_at=opened_at,
        exit_plan=exit_plan,
        current_stop=exit_plan.stop,
    )


#: How far a candidate mark may differ from the last CONFIRMED price before
#: it needs a second cycle's agreement, as a percent of that confirmed price.
#: 20%, not tuned finer than that: this is a bad-tick filter, not a
#: volatility model, and a real fast move on genuine news can plausibly clear
#: single-digit percent in one minute (see the module docstring's incident —
#: the fabricated jump there was ~30%, well past this). Overly tight
#: catches real moves too and delays real exits; the fix for that is
#: confirmation within one more cycle, not chasing a tighter number.
DEFAULT_MAX_MARK_JUMP_PCT = 20

#: How close a SECOND reading must be to the pending candidate to count as
#: agreement rather than a second, unrelated glitch. Tighter than the jump
#: threshold on purpose: confirmation should mean "the market is really
#: here now", not "also far from where it was".
_CONFIRM_TOLERANCE_PCT = 5


def sanity_checked_mark(
    *,
    candidate: Paise,
    last_confirmed: Paise | None,
    pending: Paise | None,
    max_jump_pct: float = DEFAULT_MAX_MARK_JUMP_PCT,
) -> tuple[Paise, Paise | None]:
    """Filters one cycle's fresh quote before it is trusted for a stop/
    target/trail decision. Returns `(mark_to_use, new_pending)`.

    Found live on 2026-08-04: Angel's SmartAPI websocket has documented,
    user-reported bad ticks (wrong high/low prices on real tokens). One such
    tick on an option premium — unconfirmed by any matching move in the
    underlying — was trusted immediately, fired a "target hit", and closed
    two real paper positions on a price no live order could have filled at.

    The filter: a candidate within `max_jump_pct` of the last CONFIRMED
    price is trusted immediately (this is the every-cycle common case). A
    bigger jump is quarantined — the position keeps trading on its last
    confirmed price this cycle, and the candidate is remembered as
    `pending`. If the NEXT cycle's candidate lands close to that pending
    value (within `_CONFIRM_TOLERANCE_PCT`), two independent readings have
    now agreed on the new level, which a single bad tick cannot do — that
    candidate is accepted as real. A candidate that does NOT confirm the
    previous one becomes the new pending value in its own right, so an
    outlier is never held against a later, unrelated reading.

    `last_confirmed is None` (nothing has ever priced this position) trusts
    the first reading unconditionally — there is nothing yet to compare it
    against, and refusing it would leave the position permanently
    unpriceable."""
    if last_confirmed is None:
        return candidate, None

    def _pct_diff(a: Paise, b: Paise) -> float:
        return abs(int(a) - int(b)) / int(b) * 100 if int(b) != 0 else float("inf")

    if _pct_diff(candidate, last_confirmed) <= max_jump_pct:
        return candidate, None

    if pending is not None and _pct_diff(candidate, pending) <= _CONFIRM_TOLERANCE_PCT:
        return candidate, None  # confirmed by a second, agreeing reading

    return last_confirmed, candidate


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
    if (
        plan.trailing_distance is not None
        and plan.trailing_activation is not None
        and current_premium >= plan.trailing_activation
    ):
        new_stop = next_trailing_stop(
            position.current_stop, current_premium, plan.trailing_distance, position.direction
        )

    # The profit lock is a STEPPED ratchet, distinct from the continuous
    # trail above — see `ExitPlan.profit_lock_activation`'s docstring. Each
    # time the premium gains another `activation` step it locks the stop
    # `profit_lock_buffer_pct` below the price AT THAT MOMENT (not below
    # entry — a real profit floor), then holds still until the next step.
    #
    # It fired exactly ONCE and froze until 2026-08-06. That protected the
    # first rung of profit and nothing above it: a position up 15% locked
    # roughly Rs 1,000 and then, however far it ran, could still hand back
    # everything above that one level. Asked for by the owner, who spotted
    # the gap directly: "move SL after every +15%".
    #
    # Stepped rather than continuous ON PURPOSE. The continuous Rs 3 trail
    # this project ran before closed 14 of 14 trades on `trailing_stop` at a
    # 3.1-minute average hold, because it moved on every tick and ordinary
    # noise dragged it into the price. This moves only when the trade gains
    # a further full step, so nothing between rungs can touch it.
    #
    # Combined with any trailing ratchet via `max()` so whichever mechanism
    # is more protective wins; in practice at most one of the two runs.
    profit_lock_engaged = position.profit_lock_engaged
    if plan.profit_lock_activation is not None and plan.profit_lock_buffer_pct is not None:
        activation = _next_profit_lock_rung(plan, position)
        if current_premium >= activation:
            lock_stop = Paise(current_premium - pct_of(current_premium, plan.profit_lock_buffer_pct))
            new_stop = Paise(max(new_stop, lock_stop))
            profit_lock_engaged = True

    updated = replace(position, current_stop=new_stop, profit_lock_engaged=profit_lock_engaged)

    if current_premium <= new_stop:
        if new_stop <= plan.stop:
            reason: ExitReason = "stop"
        elif profit_lock_engaged:
            # Once engaged, an elevated stop is attributed to the lock even
            # in a LATER cycle than the one that set it — the level exists
            # because of the lock, regardless of which cycle produced it.
            reason = "profit_lock"
        else:
            reason = "trailing_stop"
        return updated, ExitDecision(reason=reason, exit_premium=current_premium)

    return updated, None
