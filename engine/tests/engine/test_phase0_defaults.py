"""Pins the three Phase-0 defaults that came from a measurement or a
verified external fact rather than a preference, so a later edit has to
argue with the evidence instead of quietly overwriting it.

Sources, all recorded in `te/settings.py`'s comments alongside each field:

* `min_minutes_before_hard_exit = 40` — the median of 438 labelled winning
  firings' time-to-target, measured 2026-08-01 by
  `scripts/measure_time_to_target.py` on the 60-minute opening-range replay
  set.
* `hard_exit_by = 15:15` — Angel One's published Risk Management Policy
  force-squares intraday F&O at 15:20. A tie means the broker closes the
  position, at their price, with their charge.
* `max_consecutive_losses = 3` — a behavioural circuit breaker, explicitly
  NOT a measured edge.
"""

from __future__ import annotations

import datetime as dt

from te.settings import Settings


def test_entry_runway_is_the_measured_median() -> None:
    assert Settings().paper_cycle_min_minutes_before_hard_exit == 40


def test_hard_exit_lands_before_the_brokers_own_square_off() -> None:
    """Five minutes of clearance, not zero. At 15:20 exactly, whoever fires
    first wins — and the broker's RMS is not something this engine races."""
    settings = Settings()
    angel_rms_square_off = dt.time(15, 20)
    assert settings.paper_cycle_hard_exit_by < angel_rms_square_off
    assert settings.paper_cycle_hard_exit_by == dt.time(15, 15)


def test_the_runway_rule_and_the_hard_exit_leave_a_usable_session() -> None:
    """A guard against fixing one number into absurdity: together these two
    set the last entry time, and if that ever drifts before noon the engine
    has quietly stopped being an intraday system."""
    settings = Settings()
    hard_exit = dt.datetime.combine(dt.date(2026, 8, 3), settings.paper_cycle_hard_exit_by)
    last_entry = hard_exit - dt.timedelta(minutes=settings.paper_cycle_min_minutes_before_hard_exit)
    assert last_entry.time() == dt.time(14, 35)
    assert last_entry.time() > dt.time(12, 0)


def test_consecutive_loss_circuit_is_on_by_default() -> None:
    assert Settings().paper_cycle_max_consecutive_losses == 3


def test_the_opening_range_is_the_measured_sixty_minutes() -> None:
    """Measured across six lengths on two indices independently; both peak
    at 60 and fall away either side. See `OrbParams.opening_range_minutes`
    for the table."""
    from te.strategy.orb import OrbParams

    assert OrbParams().opening_range_minutes == 60
