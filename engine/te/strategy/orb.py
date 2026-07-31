"""`OrbStrategy` — THE primary rule (opening-range breakout). Re-derived from
`engine/strategies/orbs/orbs.py` (read as a SPECIFICATION only, per the
plan's R9) under the `te.domain.evaluation.Evaluation`/`ConditionResult`
interface, so every condition emits a real measured `actual` value.

Vocabulary (mirrors the plan's "Meta-labeling pipeline" section, so a later
meta-labeling secondary stays disjoint from it): opening range high/low over
a configurable window, breakout close beyond the range, breakout-bar volume
vs range-average volume (a volume-confirmation filter), direction.

No ML import anywhere in this file — enforced by the `lint-imports`
contract "strategy may never import ml".

`Strategy.evaluate()` returns only an `Evaluation` (the Protocol's fixed
signature), but the plan also calls for the rule to "emit a Signal ... when
all conditions pass". `OrbStrategy` satisfies both: `evaluate()` always
returns the `Evaluation`, and — only when `verdict == "traded"` — also sets
`self.last_signal` to the real `Signal` that fired, for the caller
(`te.engine.cycle`) to read immediately afterwards and hand to
`te.risk.sizing`. `last_signal` is reset to `None` at the start of every
`evaluate()` call, so a stale signal from a previous cycle can never leak
forward.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from decimal import Decimal

from te.domain.clock import DEFAULT_SESSION, IST
from te.domain.evaluation import ConditionResult, Evaluation
from te.domain.money import Paise
from te.domain.signal import Direction, Signal
from te.strategy.context import StrategyContext

_NOT_REACHED = "not reached"


def _not_reached(label: str) -> ConditionResult:
    return ConditionResult(
        label=label, required="n/a — short-circuited by an earlier failed/unreached condition", actual=_NOT_REACHED,
        passed=False, evaluated=False,
    )


@dataclass(frozen=True)
class OrbParams:
    opening_range_minutes: int = 15
    min_opening_bars: int = 3
    volume_confirmation_multiple: Decimal = Decimal("1.0")
    lot_size: int = 65


class OrbStrategy:
    name = "orb"

    def __init__(self, params: OrbParams | None = None) -> None:
        self.params = params or OrbParams()
        self.last_signal: Signal | None = None

    def evaluate(self, ctx: StrategyContext) -> Evaluation:
        self.last_signal = None
        params = self.params

        as_of_local = ctx.as_of.astimezone(IST)
        session_date = as_of_local.date()
        market_open = dt.datetime.combine(session_date, DEFAULT_SESSION.start, tzinfo=IST)
        range_end = market_open + dt.timedelta(minutes=params.opening_range_minutes)

        conditions: list[ConditionResult] = []
        bars = ctx.bars(lookback=dt.timedelta(hours=7))

        if bars.empty:
            opening_bars = bars
        else:
            local_ts = bars["event_ts"].dt.tz_convert(IST)
            opening_bars = bars[(local_ts >= market_open) & (local_ts < range_end)]

        data_cond = ConditionResult(
            label="opening range data available",
            required=f">= {params.min_opening_bars} bars in the first {params.opening_range_minutes}m after open",
            actual=f"{len(opening_bars)} bars",
            passed=len(opening_bars) >= params.min_opening_bars,
            evaluated=True,
        )
        conditions.append(data_cond)
        if not data_cond.passed:
            conditions.append(_not_reached("breakout close beyond opening range"))
            conditions.append(_not_reached("breakout volume confirmation"))
            return self._skip(ctx, conditions, "opening range not yet formed (insufficient bars)")

        range_high = float(opening_bars["h"].max())
        range_low = float(opening_bars["l"].min())
        avg_range_volume = float(opening_bars["v"].mean())

        local_ts = bars["event_ts"].dt.tz_convert(IST)
        # Drop duplicate timestamps (keeping the latest write) BEFORE the
        # edge comparison below. Two rows sharing an `event_ts` are the same
        # minute, and comparing a bar against a duplicate of itself would
        # read as "the previous bar was already beyond the range" and
        # silently suppress a real breakout. Duplicates are reachable live:
        # a WS reconnect or a retried recorder flush can re-append a minute
        # that is already stored.
        breakout_bars = bars[local_ts >= range_end].drop_duplicates(subset="event_ts", keep="last")
        if breakout_bars.empty:
            conditions.append(_not_reached("breakout close beyond opening range"))
            conditions.append(_not_reached("breakout volume confirmation"))
            return self._skip(ctx, conditions, "no bar has closed after the opening range window yet")

        breakout_bar = breakout_bars.iloc[-1]
        close = float(breakout_bar["c"])
        volume = float(breakout_bar["v"])

        # EDGE-triggered, not level-triggered. The breakout is the moment
        # price CROSSES out of the range — the first bar to close beyond it —
        # not every subsequent bar that merely remains beyond it.
        #
        # Found live on 2026-07-31: reading only `iloc[-1]` asks "is price
        # outside the range right now?", which stays true for as long as the
        # move lasts, so a single 10:00 breakout that held until 10:30 re-
        # signalled ~30 times. Each re-signal opened a fresh position (and
        # often re-stopped it) at full round-trip cost. Standard ORB is an
        # entry on the first close beyond the range — see the strategy
        # literature, which uniformly describes "wait for a candle to CLOSE
        # beyond the range" as the trigger.
        #
        # Comparing against the immediately preceding bar (rather than
        # remembering that we already fired) also keeps this rule PURE and
        # stateless, which matters because `te.strategy.registry` hands out a
        # fresh `OrbStrategy` per evaluation — instance state cannot persist
        # across cycles by design. It additionally makes a genuine SECOND
        # breakout tradeable: if price falls back inside the range and later
        # breaks out again, that is a real new crossing and should signal.
        prev_close_inside = True
        if len(breakout_bars) >= 2:
            prev_close = float(breakout_bars.iloc[-2]["c"])
            prev_close_inside = range_low <= prev_close <= range_high

        closed_outside = close > range_high or close < range_low
        direction: Direction | None
        if closed_outside and prev_close_inside:
            direction = "long_call" if close > range_high else "long_put"
        else:
            direction = None

        if closed_outside and not prev_close_inside:
            actual = (
                f"close={close:.2f} is beyond range=[{range_low:.2f}, {range_high:.2f}], but the previous "
                f"bar closed beyond it too — the crossing already happened, this is not a new breakout"
            )
        else:
            actual = f"close={close:.2f}, range=[{range_low:.2f}, {range_high:.2f}]"

        breakout_cond = ConditionResult(
            label="breakout close beyond opening range",
            required=(
                f"a bar CROSSING out: close > {range_high:.2f} (long_call) or close < {range_low:.2f} "
                f"(long_put), with the previous bar closed inside the range"
            ),
            actual=actual,
            passed=direction is not None,
            evaluated=True,
        )
        conditions.append(breakout_cond)
        if direction is None:
            conditions.append(_not_reached("breakout volume confirmation"))
            reason = (
                "already beyond the opening range — the breakout crossing happened on an earlier bar"
                if closed_outside
                else "close finished inside the opening range — no breakout"
            )
            return self._skip(ctx, conditions, reason)

        # An INDEX has no traded volume of its own — only its derivatives do.
        # Both OpenAlgo's WS feed and its history endpoint return `volume=0`
        # for `NSE_INDEX`/`BSE_INDEX` symbols (verified against stored parquet
        # and the live API, 2026-07-31). With `avg_range_volume == 0` the
        # threshold is 0 and `volume >= 0` is vacuously true, so this
        # condition silently PASSED on every single evaluation while
        # presenting itself as a filter that had been applied.
        #
        # Reporting that as `evaluated=True, passed=True` is the kind of
        # unearned number this project exists to not produce. When there is
        # no volume to confirm, say so and mark the condition NOT evaluated;
        # it still does not block (there is no evidence to block on), but it
        # no longer claims a check that never happened. On an instrument that
        # does carry volume the original comparison is unchanged.
        volume_available = avg_range_volume > 0
        if volume_available:
            threshold = avg_range_volume * float(params.volume_confirmation_multiple)
            volume_cond = ConditionResult(
                label="breakout volume confirmation",
                required=f">= {params.volume_confirmation_multiple}x opening-range average volume "
                f"({avg_range_volume:.0f})",
                actual=f"breakout bar volume={volume:.0f}",
                passed=volume >= threshold,
                evaluated=True,
            )
        else:
            volume_cond = ConditionResult(
                label="breakout volume confirmation",
                required=f">= {params.volume_confirmation_multiple}x opening-range average volume",
                actual=(
                    f"not evaluated — {ctx.instrument} reports no volume on its bars "
                    f"(an index has no traded volume of its own, only its derivatives do)"
                ),
                passed=True,
                evaluated=False,
            )
        conditions.append(volume_cond)
        if not volume_cond.passed:
            return self._skip(ctx, conditions, "breakout volume below confirmation threshold")

        self.last_signal = Signal(
            strategy=self.name,
            instrument=ctx.instrument,
            direction=direction,
            entry_premium=Paise(int(round(close * 100))),
            lot_size=params.lot_size,
            ts=ctx.as_of,
        )
        return Evaluation(
            id=self._eval_id(ctx),
            timestamp=ctx.as_of,
            strategy=self.name,
            instrument=ctx.instrument,
            verdict="traded",
            reason="opening range breakout confirmed with volume",
            conditions=tuple(conditions),
        )

    def _skip(self, ctx: StrategyContext, conditions: list[ConditionResult], reason: str) -> Evaluation:
        return Evaluation(
            id=self._eval_id(ctx),
            timestamp=ctx.as_of,
            strategy=self.name,
            instrument=ctx.instrument,
            verdict="skipped",
            reason=reason,
            conditions=tuple(conditions),
        )

    def _eval_id(self, ctx: StrategyContext) -> str:
        return f"{self.name}-{ctx.instrument}-{ctx.as_of.isoformat()}"
