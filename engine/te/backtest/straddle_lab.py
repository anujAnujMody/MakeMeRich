"""Backtests a SHORT STRADDLE with protective wings — an iron fly — entered
at a fixed clock time with no directional signal at all.

### Why this structure, and why no signal

Every measurement this project has produced says the entry rules are worth
nothing. All 32 registered strategies score the same as `random_entry`, the
144-cell buying sweep found no profitable setting, and the 288-cell credit
spread sweep found none either. Those all share one assumption: that
something in the price history says which way the index will go next.

Exactly one measurement here has ever come back positive, and it does not
depend on direction. Over 72,518 NIFTY strike-sessions
(`scripts.measure_overnight_edge`), a synthetic straddle — call plus put at
the same strike, so a directional move raises one leg and lowers the other
and largely cancels — lost a median 1.674% of its premium between the open
and the close, while the overnight window was flat (median 0.000%). That is
premium decaying inside the session, which is what a SELLER collects.

Nothing has yet tested selling that. The credit-spread sweep was the wrong
instrument for it: a credit spread is directional (a bull put spread is a
bullish bet), so it tested the signal layer again under a different name.
This module sells the structure the decay was actually measured on.

### Why wings are not optional

A naked short straddle needs roughly Rs 1.5-2 lakh of SPAN margin per lot,
against a Rs 50,000 account. Buying a further-out call and put caps the loss
at `(BOTH wing distances - credit)` for an intraday close, which is both the
risk and a close proxy for the margin blocked — see `score_trade` for why it
is both wings and not one. `wing_points` is therefore required, and
a deliberately wide wing is the closest this capital can get to the naked
structure the decay was measured on. That gap is real and is not papered
over: wide wings cost more premium and cap the win, so this tests a strictly
WORSE version of the measured effect.

### What the published record says, before any number here is read

The fixed-time short straddle is not a new idea — in India it is the widely
traded "9:20 straddle", and the practitioner consensus is that it stopped
working. Marketcalls' analysis reports 0.9 points per trade over 1.5 years,
accuracy down to 36%, and unprofitability after brokerage/STT; the common
explanation is crowding (everyone selling the same strike at the same
minute) plus the post-2020 volatility regime. Zerodha's own forum thread
reaches the same conclusion.

So the prior here is NEGATIVE, and this module exists to check it on our own
data with our own costs rather than to confirm a hope. Two consequences for
how it is built:

* Entry time is swept, not fixed at 9:20 — if 9:20 alone works and 9:25 does
  not, that is crowding or noise, not an edge (per `backtest-expert`:
  plateaus, not peaks).
* Results must be read year by year. An effect that was real until 2023 and
  gone afterwards averages out to something mildly positive over five years
  and would be actively misleading as a single number.

### The approximations, stated rather than discovered later

1. **Marks are bar CLOSES, not highs/lows.** A per-leg stop could in
   principle trigger intrabar. Using closes means a stop fires at the first
   minute whose CLOSE breached it, which is what a real stop-market order
   fills near, and avoids inventing a combined price for legs whose highs
   did not occur in the same minute.
2. **Wings are held to the end.** They are protection, not a position with
   its own stop, so they are bought at entry and sold at the final exit.
3. **All legs are costed, every time**, through the real dated `CostModel`.
"""

from __future__ import annotations

import datetime as dt
from bisect import bisect_right
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

import pandas as pd

from te.backtest.strategy_lab import _sessions
from te.data.asof import bars_asof
from te.data.barstore import BarStore
from te.data.option_history import OptionContractIndex
from te.domain.clock import IST
from te.domain.costs import CostModel
from te.domain.money import Paise
from te.domain.symbols import ParsedOptionSymbol

#: `TrialLedger` scope, separate from the buying and credit-spread scopes
#: for the reason spelled out in `te.backtest.spread_lab`: a search in one
#: instrument does not make a discovery in a different one less surprising.
TRIAL_SCOPE = "straddle_backtest"

#: Latest a position is held. Matches the live engine's
#: `paper_cycle_hard_exit_by` neighbourhood and the published 9:20-straddle
#: convention (square off 15:15), well before the closing-auction illiquidity.
DEFAULT_EXIT_BY = dt.time(15, 15)

#: Every way a position can end. A bare `str` let a downstream bucket
#: silently count zero for a reason spelled differently than the code
#: emits it.
type ExitReason = Literal["both_legs_stopped", "target", "one_leg_stopped", "time"]


@dataclass(frozen=True)
class StraddleGeometry:
    #: Wall-clock IST minute the straddle is sold. No signal, no condition —
    #: this is the whole entry rule.
    entry_time: dt.time
    #: How far out (INDEX POINTS, not strike counts) the protective wings
    #: sit. Points because our archive's strike chains have holes, so "N
    #: strikes out" is not a fixed distance — see
    #: `OptionContractIndex.nearest`'s `otm_points`.
    wing_points: Decimal
    #: Per-leg stop, as a FRACTION of that leg's own entry premium: `0.25`
    #: is the standard practitioner 25%. When one leg stops the other is
    #: left running, which is the documented behaviour of the strategy and
    #: the reason it is not simply "a combined stop": a trending day stops
    #: the losing leg early while the winning leg keeps decaying.
    #: `None` runs it with no per-leg stop at all.
    #:
    #: A FRACTION, not a percent, and renamed to say so. It was
    #: `leg_stop_pct` holding `25` while `take_profit_fraction` beside it
    #: held `0.5` — two adjacent fields, same suffix, scales differing by
    #: 100x, with only arithmetic thirty lines away to tell them apart.
    #: Both misreadings are silent: `0.25` on the percent scale stops every
    #: leg in the first minute, and `50` on the fraction scale makes the
    #: target unreachable so every cell degrades to a time exit under a
    #: "tp50" label. `__post_init__` now range-checks both, which is what
    #: makes the shared scale safe rather than merely tidy.
    leg_stop_fraction: Decimal | None
    #: Close everything once this fraction of the entry credit has been
    #: captured. `None` holds to `exit_by`.
    take_profit_fraction: Decimal | None
    #: Days-to-expiry band, INCLUSIVE. Split rather than pooled for the same
    #: reason as `SpreadGeometry`: a 0-day and a 6-day straddle have
    #: different decay and gamma, and averaging them describes neither.
    min_days_to_expiry: int = 0
    max_days_to_expiry: int = 7
    exit_by: dt.time = DEFAULT_EXIT_BY

    def __post_init__(self) -> None:
        """Rejects a misconfigured cell loudly.

        Every check here guards a failure that is otherwise SILENT: a bad
        value produces a plausible-looking row rather than an error. A
        reversed expiry band reports "0 trades, N unresolved", which reads
        as a thin archive; a zero wing reports a full sweep of refusals; a
        mis-scaled fraction reports a complete population of trades that
        all exited the same wrong way. None of those announce themselves,
        and a sweep is 288 cells deep.
        """
        if self.wing_points <= 0:
            raise ValueError(f"wing_points must be positive, got {self.wing_points}")
        if self.min_days_to_expiry > self.max_days_to_expiry:
            raise ValueError(
                f"expiry band is reversed: min_days_to_expiry={self.min_days_to_expiry} > "
                f"max_days_to_expiry={self.max_days_to_expiry}"
            )
        if self.min_days_to_expiry < 0:
            raise ValueError(f"min_days_to_expiry must not be negative, got {self.min_days_to_expiry}")
        if self.entry_time >= self.exit_by:
            raise ValueError(f"entry_time {self.entry_time} is not before exit_by {self.exit_by}")
        # The upper bound is 1, not 100: both fields are fractions. A value
        # above 1 is the percent scale leaking in, and that is exactly the
        # confusion the rename exists to stop.
        if self.leg_stop_fraction is not None and not (Decimal(0) < self.leg_stop_fraction <= Decimal(2)):
            raise ValueError(
                f"leg_stop_fraction is a FRACTION of the leg's entry premium (0.25 = 25%), "
                f"got {self.leg_stop_fraction}"
            )
        if self.take_profit_fraction is not None and not (Decimal(0) < self.take_profit_fraction < Decimal(1)):
            raise ValueError(
                f"take_profit_fraction is a FRACTION of the credit (0.5 = half), "
                f"got {self.take_profit_fraction}"
            )

    def __str__(self) -> str:
        stop = "none" if self.leg_stop_fraction is None else f"{self.leg_stop_fraction}"
        take = "none" if self.take_profit_fraction is None else f"{self.take_profit_fraction}"
        return (
            f"{self.entry_time:%H:%M}/wing{self.wing_points}/sl{stop}/tp{take}"
            f"/dte{self.min_days_to_expiry}-{self.max_days_to_expiry}"
        )


@dataclass(frozen=True)
class StraddleTrade:
    entry_ts: dt.datetime
    day: dt.date
    expiry: dt.date
    strike: Decimal
    #: Net credit per unit at entry: both shorts sold, both wings bought.
    entry_credit: Paise
    exit_reason: ExitReason
    #: Per unit (i.e. before x lot_size), paise, AFTER every leg's real
    #: brokerage/STT/GST.
    net_pnl_per_unit: Paise
    #: `(wing distance - credit)`, the structural maximum loss per unit and
    #: the figure the account has to be able to afford.
    max_loss_per_unit: Paise
    r_multiple: float
    lot_size: int


def _premium_at(store: BarStore, symbol: str, at: dt.datetime) -> Paise | None:
    visible = bars_asof(store, symbol, at, dt.timedelta(minutes=10), interval="1m")
    if visible.empty:
        return None
    return Paise(int(round(float(visible.iloc[-1]["c"]) * 100)))


def _closes(store: BarStore, symbol: str, *, entry_ts: dt.datetime, until: dt.datetime) -> dict[pd.Timestamp, float]:
    span = until - entry_ts + dt.timedelta(minutes=1)
    bars = bars_asof(store, symbol, until, span, interval="1m")
    return {row["event_ts"]: float(row["c"]) for _, row in bars.iterrows()}


@dataclass(frozen=True)
class _Legs:
    call: ParsedOptionSymbol
    put: ParsedOptionSymbol
    call_wing: ParsedOptionSymbol
    put_wing: ParsedOptionSymbol


def resolve_legs(
    contracts: OptionContractIndex, *, on: dt.date, index_level: Decimal, geometry: StraddleGeometry
) -> _Legs | str:
    """The four contracts, or `None` when the archive cannot support this
    structure on this day.

    Every leg must share ONE expiry. Legs on different expiries are not an
    iron fly — the wings would not cap anything on the day the shorts
    expire — so a mismatch is refused rather than traded as though the risk
    were still bounded.
    """
    call = contracts.nearest(
        on=on, index_level=index_level, option_type="CE",
        otm_points=Decimal(0), max_days_to_expiry=geometry.max_days_to_expiry,
        min_days_to_expiry=geometry.min_days_to_expiry,
    )
    put = contracts.nearest(
        on=on, index_level=index_level, option_type="PE",
        otm_points=Decimal(0), max_days_to_expiry=geometry.max_days_to_expiry,
        min_days_to_expiry=geometry.min_days_to_expiry,
    )
    if call is None or put is None:
        return "short_leg_not_listed"
    # A straddle is ONE strike. `nearest` rounds each side independently, so
    # a spot sitting exactly between two strikes can round the call up and
    # the put down and silently produce a strangle — a different structure
    # with different decay, which would then be reported as a straddle.
    if call.strike != put.strike:
        return "would_be_a_strangle"
    # Wings are measured from the SHORT STRIKE, not from spot: the structure
    # has to be symmetric around what was actually sold, or one side is
    # protected closer than the other.
    call_wing = contracts.nearest(
        on=on, index_level=call.strike, option_type="CE",
        otm_points=geometry.wing_points, max_days_to_expiry=geometry.max_days_to_expiry,
        min_days_to_expiry=geometry.min_days_to_expiry,
    )
    put_wing = contracts.nearest(
        on=on, index_level=put.strike, option_type="PE",
        otm_points=geometry.wing_points, max_days_to_expiry=geometry.max_days_to_expiry,
        min_days_to_expiry=geometry.min_days_to_expiry,
    )
    if call_wing is None or put_wing is None:
        return "wing_not_listed"
    if not (call.expiry == put.expiry == call_wing.expiry == put_wing.expiry):
        return "legs_on_different_expiries"
    # A wing must actually sit BEYOND the strike it protects.
    #
    # `nearest(otm_points=...)` accepts any listed strike within half a
    # strike step of the target, and on a coarse or holed chain that
    # rounding can land the "wing" back on the short strike itself.
    # Measured on the real NIFTY archive 2026-08-05: 1 session in 575
    # resolved a zero-width wing on every requested distance.
    #
    # Zero width is not a narrow fly, it is a NAKED short leg — no
    # protection, unbounded loss — and it would still have been scored with
    # a `2 x wing_points` risk unit, i.e. sized as though fully hedged. The
    # symmetric case is caught downstream by `credit <= 0`, but a hole on
    # one side only produces a half-naked structure that nothing else
    # rejects.
    if call_wing.strike <= call.strike or put_wing.strike >= put.strike:
        return "wing_collapsed_onto_short"
    return _Legs(call=call, put=put, call_wing=call_wing, put_wing=put_wing)


@dataclass(frozen=True)
class _Walk:
    """Entry and exit prices for all four legs, plus why it ended."""

    call_entry: Paise
    put_entry: Paise
    call_wing_entry: Paise
    put_wing_entry: Paise
    call_exit: Paise
    put_exit: Paise
    call_wing_exit: Paise
    put_wing_exit: Paise
    reason: ExitReason


@dataclass(frozen=True)
class SessionPosition:
    """One session's four legs, priced at entry and with every subsequent
    close already read.

    This is what makes a 288-cell sweep tractable. Resolving the chain and
    reading four Parquet-backed series is the expensive part and depends
    only on the entry time, the wing distance and the expiry band — 24
    combinations across the whole grid. The stop and target only READ these
    series, so the remaining 12 combinations are pure arithmetic over a
    position set that was built once. Without this split every cell re-reads
    the same bars: 288 collects instead of 24, i.e. ~12x the bar reads (the
    stop/target count) for an identical answer.
    """

    day: dt.date
    entry_ts: dt.datetime
    legs: _Legs
    lot_size: int
    entry: dict[str, Paise]
    #: `leg name -> {minute -> close}` for every minute after entry up to
    #: `exit_by`.
    closes: dict[str, dict[pd.Timestamp, float]]


def build_position(
    *, store: BarStore, legs: _Legs, entry_ts: dt.datetime, exit_by: dt.time, lot_size: int
) -> SessionPosition | str:
    """Prices all four legs at entry and reads their paths to `exit_by`, or
    `None` when a leg cannot be priced — a real property of thin far-OTM
    wings, counted by the caller rather than guessed at."""
    entries = {
        name: _premium_at(store, symbol, entry_ts)
        for name, symbol in (
            ("call", legs.call.symbol), ("put", legs.put.symbol),
            ("call_wing", legs.call_wing.symbol), ("put_wing", legs.put_wing.symbol),
        )
    }
    if any(p is None or p <= 0 for p in entries.values()):
        return "leg_unpriced_at_entry"
    day = entry_ts.astimezone(IST).date()
    until = dt.datetime.combine(day, exit_by, tzinfo=IST)
    if until <= entry_ts:
        return "entry_at_or_after_exit_by"
    closes = {
        "call": _closes(store, legs.call.symbol, entry_ts=entry_ts, until=until),
        "put": _closes(store, legs.put.symbol, entry_ts=entry_ts, until=until),
        "call_wing": _closes(store, legs.call_wing.symbol, entry_ts=entry_ts, until=until),
        "put_wing": _closes(store, legs.put_wing.symbol, entry_ts=entry_ts, until=until),
    }
    if not any(t > pd.Timestamp(entry_ts) for t in closes["call"]):
        return "no_bars_after_entry"
    return SessionPosition(
        day=day, entry_ts=entry_ts, legs=legs, lot_size=lot_size,
        entry={k: v for k, v in entries.items() if v is not None}, closes=closes,
    )


def walk(
    *, store: BarStore, legs: _Legs, entry_ts: dt.datetime, geometry: StraddleGeometry
) -> _Walk | str:
    """Convenience wrapper: build one position and label it. Reads bars, so
    a sweep should call `build_position` once and `walk_position` per
    geometry instead."""
    position = build_position(
        store=store, legs=legs, entry_ts=entry_ts, exit_by=geometry.exit_by, lot_size=1
    )
    if isinstance(position, str):
        return position
    return walk_position(position=position, geometry=geometry)


def walk_position(*, position: SessionPosition, geometry: StraddleGeometry) -> _Walk | str:
    """Minute-by-minute over an already-read position.

    Takes no cost model on purpose: the stop and target are GROSS premium
    levels, exactly as they would be entered with a broker. Charges are
    applied afterwards, on every leg, by `score_trade`.
    """
    entries = position.entry
    entry_ts = position.entry_ts
    call_entry, put_entry = entries["call"], entries["put"]
    wing_cost = int(entries["call_wing"]) + int(entries["put_wing"])
    credit = int(call_entry) + int(put_entry) - wing_cost
    if credit <= 0:
        # The wings cost more than the shorts brought in. That is a broken
        # quote, not a trade anyone would enter, and labelling it a
        # guaranteed loser would put a fabricated number in the sample.
        return "credit_not_positive"
    if credit >= int(geometry.wing_points * 100):
        # Credit at or above the wing distance means the structure cannot
        # lose — free money, which options do not offer. It is a crossed or
        # stale quote. Refused rather than traded: `max_loss` would be zero
        # or negative, and sizing off a zero risk unit takes an unbounded
        # number of lots on a price that never existed.
        return "credit_exceeds_wing"

    call_closes = position.closes["call"]
    put_closes = position.closes["put"]
    wing_call_closes = position.closes["call_wing"]
    wing_put_closes = position.closes["put_wing"]
    # Sorted ONCE per walk, not rebuilt inside the minute loop — see
    # `_wing_exit`.
    wing_call_ts = sorted(wing_call_closes)
    wing_put_ts = sorted(wing_put_closes)
    minutes = sorted(t for t in call_closes if t in put_closes and t > pd.Timestamp(entry_ts))
    if not minutes:
        return "shorts_never_traded_together"

    call_stop = (
        Paise(round(float(call_entry) * float(1 + geometry.leg_stop_fraction)))
        if geometry.leg_stop_fraction is not None else None
    )
    put_stop = (
        Paise(round(float(put_entry) * float(1 + geometry.leg_stop_fraction)))
        if geometry.leg_stop_fraction is not None else None
    )
    target_cost = (
        Paise(round(float(credit) * float(1 - geometry.take_profit_fraction)))
        if geometry.take_profit_fraction is not None else None
    )

    # `None` while a short leg is still open; set to the price it closed at
    # once its own stop fires. A stopped leg stops moving — that is the whole
    # point of the per-leg stop, and marking it onward would give back the
    # loss it just locked in.
    call_closed_at: Paise | None = None
    put_closed_at: Paise | None = None
    last = minutes[-1]

    for ts in minutes:
        call_now = Paise(int(round(call_closes[ts] * 100)))
        put_now = Paise(int(round(put_closes[ts] * 100)))
        if call_closed_at is None and call_stop is not None and call_now >= call_stop:
            call_closed_at = call_now
        if put_closed_at is None and put_stop is not None and put_now >= put_stop:
            put_closed_at = put_now

        live_call = call_closed_at if call_closed_at is not None else call_now
        live_put = put_closed_at if put_closed_at is not None else put_now
        if call_closed_at is not None and put_closed_at is not None:
            return _Walk(
                call_entry=call_entry, put_entry=put_entry,
                call_wing_entry=entries["call_wing"], put_wing_entry=entries["put_wing"],
                call_exit=call_closed_at, put_exit=put_closed_at,
                call_wing_exit=_wing_exit(wing_call_ts, wing_call_closes, ts, entries["call_wing"]),
                put_wing_exit=_wing_exit(wing_put_ts, wing_put_closes, ts, entries["put_wing"]),
                reason="both_legs_stopped",
            )
        if target_cost is not None:
            # Cost to close the whole structure: buy the shorts back, sell
            # the wings. Compared against the target as a GROSS level.
            cost_to_close = (
                int(live_call) + int(live_put)
                - int(_wing_exit(wing_call_ts, wing_call_closes, ts, entries["call_wing"]))
                - int(_wing_exit(wing_put_ts, wing_put_closes, ts, entries["put_wing"]))
            )
            if cost_to_close <= int(target_cost):
                return _Walk(
                    call_entry=call_entry, put_entry=put_entry,
                    call_wing_entry=entries["call_wing"], put_wing_entry=entries["put_wing"],
                    call_exit=live_call, put_exit=live_put,
                    call_wing_exit=_wing_exit(wing_call_ts, wing_call_closes, ts, entries["call_wing"]),
                    put_wing_exit=_wing_exit(wing_put_ts, wing_put_closes, ts, entries["put_wing"]),
                    reason="target",
                )

    return _Walk(
        call_entry=call_entry, put_entry=put_entry,
        call_wing_entry=entries["call_wing"], put_wing_entry=entries["put_wing"],
        call_exit=call_closed_at if call_closed_at is not None else Paise(int(round(call_closes[last] * 100))),
        put_exit=put_closed_at if put_closed_at is not None else Paise(int(round(put_closes[last] * 100))),
        call_wing_exit=_wing_exit(wing_call_ts, wing_call_closes, last, entries["call_wing"]),
        put_wing_exit=_wing_exit(wing_put_ts, wing_put_closes, last, entries["put_wing"]),
        reason="one_leg_stopped" if (call_closed_at is not None) != (put_closed_at is not None) else "time",
    )


def _wing_exit(
    sorted_ts: list[pd.Timestamp], closes: dict[pd.Timestamp, float], at: pd.Timestamp, entry: Paise
) -> Paise:
    """The wing's price at `at`, or the last one recorded before it.

    A far-OTM wing routinely goes minutes without a print, and dropping the
    whole trade because the PROTECTIVE leg was quiet would throw away real
    trades for a reason that has nothing to do with the result.

    ### The bias is NOT reliably conservative — an earlier version of this
    ### docstring claimed it was, and that was wrong.

    Wing exits enter gross P&L with a POSITIVE sign (`score_trade`), so a
    substituted price that is too LOW is pessimistic and one that is too
    HIGH is optimistic. Which occurs is not fixed:

    * The wing that stops printing is usually the one moving AWAY from spot
      — i.e. the one decaying. Holding its last print fixed OVER-credits it.
      On a quiet decay day, which is precisely the seller's winning day and
      the whole thesis of this module, the bias is therefore OPTIMISTIC.
    * On a gap day the wing that goes quiet can be the one gaining, and the
      bias flips pessimistic.
    * If a wing never prints after entry at all, it is scored flat for the
      whole session — the structure is measured as a NAKED straddle that
      merely paid for wings.

    So the direction depends on the day, and the optimistic branch covers
    the days the strategy is supposed to win. Callers must count the
    substitutions rather than reason about the sign — `label_positions`
    does, and reports them.

    `sorted_ts` is the wing's timestamps pre-sorted once per position.
    Rebuilding and re-scanning that list per call made this O(n^2) per
    position and ate the speedup `SessionPosition` exists to provide.
    """
    index = bisect_right(sorted_ts, at)
    if index == 0:
        return entry
    return Paise(int(round(closes[sorted_ts[index - 1]] * 100)))


def score_trade(
    *,
    walked: _Walk,
    legs: _Legs,
    lot_size: int,
    exchange: str,
    cost_model: CostModel,
    on: dt.date,
) -> tuple[Paise, Paise, Paise, float]:
    """`(entry_credit, net_pnl_per_unit, max_loss_per_unit, r_multiple)`.

    EIGHT legs are costed: sell two shorts and buy two wings to open, then
    the reverse to close. A structure that is cheap to be right about and
    expensive to trade is exactly what this project keeps rediscovering, so
    none of the eight is waved through.

    Takes `legs` rather than the requested `wing_points`, because the risk
    unit must describe the contracts that were actually resolved. The two
    can differ: `nearest(otm_points=...)` accepts a strike within half a
    strike step of the target, and it rounds the call and put sides
    independently, so the two wings need not even be the same width. Sizing
    off the REQUESTED distance would be sizing off a structure nobody
    traded.
    """
    entry_credit = Paise(
        int(walked.call_entry) + int(walked.put_entry)
        - int(walked.call_wing_entry) - int(walked.put_wing_entry)
    )
    charges = Paise(
        int(cost_model.leg(side="SELL", premium=walked.call_entry, qty=lot_size, exchange=exchange, on=on).total)
        + int(cost_model.leg(side="SELL", premium=walked.put_entry, qty=lot_size, exchange=exchange, on=on).total)
        + int(cost_model.leg(side="BUY", premium=walked.call_wing_entry, qty=lot_size, exchange=exchange, on=on).total)
        + int(cost_model.leg(side="BUY", premium=walked.put_wing_entry, qty=lot_size, exchange=exchange, on=on).total)
        + int(cost_model.leg(side="BUY", premium=walked.call_exit, qty=lot_size, exchange=exchange, on=on).total)
        + int(cost_model.leg(side="BUY", premium=walked.put_exit, qty=lot_size, exchange=exchange, on=on).total)
        + int(cost_model.leg(side="SELL", premium=walked.call_wing_exit, qty=lot_size, exchange=exchange, on=on).total)
        + int(cost_model.leg(side="SELL", premium=walked.put_wing_exit, qty=lot_size, exchange=exchange, on=on).total)
    )
    gross_total = (
        (int(walked.call_entry) - int(walked.call_exit))
        + (int(walked.put_entry) - int(walked.put_exit))
        + (int(walked.call_wing_exit) - int(walked.call_wing_entry))
        + (int(walked.put_wing_exit) - int(walked.put_wing_entry))
    ) * lot_size
    net_total = gross_total - int(charges)
    net_per_unit = Paise(round(net_total / lot_size))

    # TWO wing distances, not one — and this is the correction that matters
    # most in this file.
    #
    # The obvious figure is `wing - credit`: at EXPIRY the index sits on one
    # side, so one vertical is fully in the money and the other is
    # worthless. That bound is real and it is also irrelevant here, because
    # this strategy never reaches expiry — it closes intraday, and intraday
    # BOTH verticals carry value at once. An iron fly is a short call spread
    # plus a short put spread, each worth at most the wing distance, so the
    # cost to close the pair is bounded by `2 x wing`, not by `wing`.
    #
    # On a 100-point wing with a Rs 86 credit the expiry figure says Rs 14
    # per unit is at stake; the intraday figure says Rs 114, and the
    # intraday figure is the one a position closed at 15:15 actually pays.
    #
    # This is deliberately the conservative bound rather than a
    # stop-implied estimate. A per-leg stop caps the loss in the ordinary
    # case, but it is a level, not a guarantee: a gap through it fills
    # worse, and sizing off the level would assume away exactly the day
    # that hurts.
    #
    # WHAT THIS BOUND DOES NOT COVER, stated plainly because the first
    # version of this comment overstated it:
    #
    #   * It is GROSS. `net_per_unit` above subtracts eight legs of
    #     brokerage/STT/GST, so `|net| > max_loss` is systematically
    #     reachable on a full-width loss. `label_positions` counts how often
    #     that happens so it is visible rather than averaged away.
    #   * `_wing_exit` can substitute a stale wing price (see its
    #     docstring), and a pair scored with a moved short and an unmoved
    #     wing is not arbitrage-free. That can exceed the bound in either
    #     direction and is a property of the archive, not of the structure.
    #
    # An earlier draft cited a Rs 30,888 single-day loss against a Rs 1,500
    # budget as proof this bound is sound. That anecdote does NOT reconcile
    # and has been removed: at the old `wing - credit` unit a Rs 1,500
    # budget buys one lot, whose worst case under even this bound is
    # Rs 8,550. The real figure came from compounded equity raising the lot
    # count plus stale-wing scoring — i.e. from the two caveats above, not
    # from the expiry-vs-intraday distinction it was cited to justify.
    call_width = int((legs.call_wing.strike - legs.call.strike) * 100)
    put_width = int((legs.put.strike - legs.put_wing.strike) * 100)
    max_loss_per_unit = Paise(call_width + put_width - int(entry_credit))
    r_multiple = net_per_unit / max_loss_per_unit if max_loss_per_unit > 0 else 0.0
    return entry_credit, net_per_unit, max_loss_per_unit, r_multiple


def _index_level_at(frame: pd.DataFrame, at: dt.datetime) -> Decimal | None:
    """The index's close at or immediately before `at`. `None` when the
    session has no bar that early — a late-opening or truncated session,
    which must not be entered by guessing a level."""
    visible = frame[frame["event_ts"] <= pd.Timestamp(at)]
    if visible.empty:
        return None
    return Decimal(str(float(visible.iloc[-1]["c"])))


def collect_positions(
    *,
    store: BarStore,
    contracts: OptionContractIndex,
    lot_size_for: Callable[[dt.date], int],
    geometry: StraddleGeometry,
    instrument: str = "NIFTY",
    since: dt.date | None = None,
    until: dt.date | None = None,
) -> tuple[list[SessionPosition], Counter[str]]:
    """`(positions, unresolved_by_reason)` — one candidate per session.

    `unresolved_by_reason` is a `Counter`, not an integer, because the
    causes demand opposite responses and a single number cannot tell them
    apart. "321 of 637 unresolved" was reported for weeks and meant nothing:
    `wing_not_listed` says narrow the grid, `credit_exceeds_wing` says the
    quotes are broken and every surviving trade in that cell is drawn from
    a quote-quality-filtered subsample, and `no_index_bar_at_entry` says the
    session was truncated. Printing the breakdown is what makes a coverage
    problem distinguishable from a data problem.

    Only the geometry fields that decide WHICH contracts are traded matter
    here: entry time, wing distance and the expiry band. The stop and
    target are read later by `label_positions`, so one call serves every
    stop/target combination sharing those three.

    No strategy pass at all: there is nothing to evaluate, which is the
    point of the structure. `since`/`until` restrict which sessions count,
    so a geometry chosen by looking at results can still be checked on a
    period that had no chance to influence it.
    """
    positions: list[SessionPosition] = []
    unresolved: Counter[str] = Counter()
    for day, frame in _sessions(store, instrument):
        if (since is not None and day < since) or (until is not None and day > until):
            continue
        entry_ts = dt.datetime.combine(day, geometry.entry_time, tzinfo=IST)
        index_level = _index_level_at(frame, entry_ts)
        if index_level is None:
            unresolved["no_index_bar_at_entry"] += 1
            continue
        legs = resolve_legs(contracts, on=day, index_level=index_level, geometry=geometry)
        if isinstance(legs, str):
            unresolved[legs] += 1
            continue
        position = build_position(
            store=store, legs=legs, entry_ts=entry_ts, exit_by=geometry.exit_by, lot_size=lot_size_for(day)
        )
        if isinstance(position, str):
            unresolved[position] += 1
            continue
        positions.append(position)
    return positions, unresolved


def label_positions(
    *,
    positions: list[SessionPosition],
    geometry: StraddleGeometry,
    cost_model: CostModel,
    exchange: str = "NFO",
) -> tuple[list[StraddleTrade], Counter[str]]:
    """`(trades, unresolved_by_reason)` for one stop/target combination
    over an already-collected position set. Reads no bars.

    The counter also carries `net_exceeded_max_loss`: `max_loss_per_unit` is
    a GROSS bound and `net_pnl_per_unit` is net of eight legs of charges, so
    a full-width loss can legitimately overshoot the risk unit that sizing
    divides by. That is not a bug to hide — it is a real property of the
    structure, and counting it is what stops a reported drawdown from
    understating the true one."""
    trades: list[StraddleTrade] = []
    unresolved: Counter[str] = Counter()
    for position in positions:
        walked = walk_position(position=position, geometry=geometry)
        if isinstance(walked, str):
            unresolved[walked] += 1
            continue
        credit, net, max_loss, r_multiple = score_trade(
            walked=walked, legs=position.legs, lot_size=position.lot_size,
            exchange=exchange, cost_model=cost_model, on=position.day,
        )
        trades.append(
            StraddleTrade(
                entry_ts=position.entry_ts, day=position.day, expiry=position.legs.call.expiry,
                strike=position.legs.call.strike, entry_credit=credit, exit_reason=walked.reason,
                net_pnl_per_unit=net, max_loss_per_unit=max_loss, r_multiple=r_multiple,
                lot_size=position.lot_size,
            )
        )
        if abs(int(net)) > int(max_loss):
            unresolved["net_exceeded_max_loss"] += 1
    return trades, unresolved


def run_straddle_backtest(
    *,
    store: BarStore,
    contracts: OptionContractIndex,
    cost_model: CostModel,
    lot_size_for: Callable[[dt.date], int],
    geometry: StraddleGeometry,
    instrument: str = "NIFTY",
    exchange: str = "NFO",
    since: dt.date | None = None,
    until: dt.date | None = None,
) -> tuple[list[StraddleTrade], Counter[str]]:
    """One geometry, end to end. A sweep should call `collect_positions`
    once per (entry time, wing, expiry band) and `label_positions` per
    stop/target instead — see `SessionPosition`.

    The two counters are MERGED but their keys stay distinct, so a caller
    can still tell a chain-resolution failure from a walk failure."""
    positions, unresolved = collect_positions(
        store=store, contracts=contracts, lot_size_for=lot_size_for, geometry=geometry,
        instrument=instrument, since=since, until=until,
    )
    trades, unlabelled = label_positions(
        positions=positions, geometry=geometry, cost_model=cost_model, exchange=exchange
    )
    return trades, unresolved + unlabelled
