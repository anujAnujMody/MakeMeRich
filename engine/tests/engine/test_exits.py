"""`te.engine.exits` — the plan's single most important file this phase:
every open position gets a real stop-loss, trailing stop, target, and
time-based exit, structurally, not by convention."""

from __future__ import annotations

import dataclasses
import datetime as dt
from decimal import Decimal

import pytest

from te.domain.clock import IST
from te.domain.money import Paise
from te.domain.signal import ExitPlan
from te.engine.exits import OpenPosition, evaluate_position, next_trailing_stop, open_position, sanity_checked_mark

OPENED_AT = dt.datetime(2026, 7, 29, 9, 45, tzinfo=IST)


def _plan(**overrides: object) -> ExitPlan:
    defaults: dict[str, object] = {
        "entry_premium": Paise(2_000),
        "stop": Paise(1_800),
        "trailing_distance": Paise(200),
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
        "lot_size": 65,
        "lots": 1,
        "opened_at": OPENED_AT,
    }
    with pytest.raises(TypeError):
        open_position(**kwargs)  # type: ignore[call-arg, arg-type]


def test_open_position_seeds_current_stop_from_plan() -> None:
    position = _position()
    assert position.current_stop == position.exit_plan.stop


def test_entry_premium_is_stored_once_on_the_plan() -> None:
    """`OpenPosition.entry_premium` reads through to the plan. Held in both
    places, a position and its own exit levels could disagree about what was
    paid — and the levels are derived from it."""
    position = _position()
    assert position.entry_premium == position.exit_plan.entry_premium == Paise(2_000)


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
    position = _position(exit_plan=_plan(trailing_distance=None))
    now = OPENED_AT + dt.timedelta(minutes=5)
    updated, decision = evaluate_position(position, current_premium=Paise(2_500), now=now)

    assert decision is None
    assert updated.current_stop == position.exit_plan.stop  # never ratcheted


class TestProfitLock:
    """The ONE-TIME profit lock — distinct from trailing (which never fires
    in these tests: `trailing_distance=None` throughout). Entry 2000, stop
    1800, target 3000, activation 2300 (15% above entry), buffer 5%: at
    activation the lock sets stop = 2300 - 5%*2300 = 2185."""

    def _plan_with_lock(self, **overrides: object) -> ExitPlan:
        defaults: dict[str, object] = {
            "trailing_distance": None,
            "profit_lock_activation": Paise(2_300),
            "profit_lock_buffer_pct": Decimal(5),
        }
        defaults.update(overrides)
        return _plan(**defaults)

    def test_below_activation_the_original_stop_governs_untouched(self) -> None:
        position = _position(exit_plan=self._plan_with_lock())
        now = OPENED_AT + dt.timedelta(minutes=5)
        updated, decision = evaluate_position(position, current_premium=Paise(2_100), now=now)

        assert decision is None
        assert updated.current_stop == Paise(1_800)  # unchanged
        assert updated.profit_lock_engaged is False

    def test_crossing_activation_locks_the_stop_below_that_price_not_entry(self) -> None:
        position = _position(exit_plan=self._plan_with_lock())
        now = OPENED_AT + dt.timedelta(minutes=5)
        updated, decision = evaluate_position(position, current_premium=Paise(2_300), now=now)

        assert decision is None  # 2300 is above the new stop (2185), position stays open
        assert updated.current_stop == Paise(2_185)
        assert updated.profit_lock_engaged is True

    def test_a_dip_through_the_locked_stop_exits_at_a_real_profit(self) -> None:
        position = _position(exit_plan=self._plan_with_lock())
        t1 = OPENED_AT + dt.timedelta(minutes=5)
        position, decision = evaluate_position(position, current_premium=Paise(2_300), now=t1)
        assert decision is None

        t2 = OPENED_AT + dt.timedelta(minutes=10)
        updated, decision = evaluate_position(position, current_premium=Paise(2_150), now=t2)

        assert decision is not None
        assert decision.reason == "profit_lock"
        assert decision.exit_premium == Paise(2_150)
        assert int(decision.exit_premium) > int(Paise(2_000))  # a real profit above entry, not breakeven

    def test_the_lock_is_one_time_it_does_not_keep_ratcheting_on_new_highs(self) -> None:
        """The defining difference from a real trailing stop: once engaged,
        further favorable moves do NOT raise the stop again."""
        position = _position(exit_plan=self._plan_with_lock())
        t1 = OPENED_AT + dt.timedelta(minutes=5)
        position, decision = evaluate_position(position, current_premium=Paise(2_300), now=t1)
        assert position.current_stop == Paise(2_185)

        t2 = OPENED_AT + dt.timedelta(minutes=10)
        position, decision = evaluate_position(position, current_premium=Paise(2_800), now=t2)

        assert decision is None
        assert position.current_stop == Paise(2_185), "must not move again after the one-time lock fired"

    def test_never_loosens_the_original_stop(self) -> None:
        """A degenerate buffer_pct that would compute a lock BELOW the
        original stop must never make the position's real risk worse."""
        plan = self._plan_with_lock(profit_lock_activation=Paise(1_850), profit_lock_buffer_pct=Decimal(50))
        position = _position(exit_plan=plan)
        now = OPENED_AT + dt.timedelta(minutes=5)
        updated, decision = evaluate_position(position, current_premium=Paise(1_850), now=now)

        # lock candidate = 1850 - 50% = 925, far below the original 1800 stop
        assert updated.current_stop == Paise(1_800), "the higher of the two floors must win"

    def test_a_position_with_no_profit_lock_configured_is_unaffected(self) -> None:
        """The default shape — must behave exactly as before this feature
        existed."""
        position = _position(exit_plan=_plan(trailing_distance=None))
        now = OPENED_AT + dt.timedelta(minutes=5)
        updated, decision = evaluate_position(position, current_premium=Paise(2_900), now=now)

        assert decision is None
        assert updated.current_stop == Paise(1_800)
        assert updated.profit_lock_engaged is False


class TestSanityCheckedMark:
    """Found live on 2026-08-04: Angel's SmartAPI websocket has documented,
    user-reported bad ticks. A single spurious premium print on
    NIFTY04AUG2624600PE — the underlying itself barely moved in the same
    minute — was trusted immediately by `run_exit_cycle`, fired a "target
    hit", and closed two real paper positions on a price no live order could
    have filled at. Two consecutive readings agreeing is what a single bad
    tick cannot fake."""

    def test_the_first_ever_reading_is_trusted_unconditionally(self) -> None:
        """Nothing to compare it against yet — refusing it would leave a
        brand new position permanently unpriceable."""
        mark, pending = sanity_checked_mark(candidate=Paise(4_000), last_confirmed=None, pending=None)
        assert mark == Paise(4_000)
        assert pending is None

    def test_a_small_move_is_trusted_immediately(self) -> None:
        mark, pending = sanity_checked_mark(candidate=Paise(4_200), last_confirmed=Paise(4_000), pending=None)
        assert mark == Paise(4_200)
        assert pending is None

    def test_a_big_unconfirmed_jump_is_quarantined_not_trusted(self) -> None:
        """The exact 2026-08-04 shape: candidate is far past the jump
        threshold from the last confirmed price, with nothing yet
        corroborating it. The position must keep trading on the OLD,
        trusted price this cycle — not the spike."""
        mark, pending = sanity_checked_mark(candidate=Paise(6_180), last_confirmed=Paise(4_675), pending=None)
        assert mark == Paise(4_675), "must not act on an unconfirmed spike"
        assert pending == Paise(6_180), "the candidate must be remembered for next cycle's comparison"

    def test_a_second_reading_that_agrees_confirms_the_move(self) -> None:
        """A real, fast move reappears at roughly the same level on the very
        next quote — a single bad tick cannot reproduce itself like that."""
        mark, pending = sanity_checked_mark(candidate=Paise(6_155), last_confirmed=Paise(4_675), pending=Paise(6_180))
        assert mark == Paise(6_155), "two independent readings agreeing must be trusted"
        assert pending is None

    def test_a_second_reading_that_disagrees_replaces_the_pending_candidate(self) -> None:
        """The pending spike does NOT confirm a totally different second
        outlier — that would let one bad tick vouch for an unrelated one.
        The new, different candidate becomes its own pending value instead
        of being trusted on the strength of a stranger."""
        mark, pending = sanity_checked_mark(candidate=Paise(3_000), last_confirmed=Paise(4_675), pending=Paise(6_180))
        assert mark == Paise(4_675), "still not confirmed — this is a DIFFERENT outlier, not agreement"
        assert pending == Paise(3_000)

    def test_a_jump_back_toward_the_confirmed_price_is_trusted_immediately(self) -> None:
        """A candidate that lands back within the normal band of the last
        CONFIRMED price is real by the same rule as any small move — it does
        not need to match `pending` first."""
        mark, pending = sanity_checked_mark(candidate=Paise(4_700), last_confirmed=Paise(4_675), pending=Paise(6_180))
        assert mark == Paise(4_700)
        assert pending is None

    def test_the_jump_threshold_is_configurable(self) -> None:
        """A caller with a different risk tolerance can widen or narrow the
        bad-tick threshold without editing this module."""
        mark, pending = sanity_checked_mark(
            candidate=Paise(4_500), last_confirmed=Paise(4_000), pending=None, max_jump_pct=5
        )
        assert mark == Paise(4_000), "12.5% move exceeds a tightened 5% threshold"
        assert pending == Paise(4_500)
