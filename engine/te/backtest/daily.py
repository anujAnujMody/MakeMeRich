"""Rupees-per-day reporting — the missing translation from R-multiples and
Sharpe (what every other module in `te.backtest` reports) to "how much money
per day, and what was the worst day", in PAISE, net of real costs.

`daily_net_from_trades()` collapses a trade list down to one net-paise figure
per IST calendar day (keyed by when the trade CLOSED); `build_daily_report()`
turns that day-by-day mapping into a `DailyReport`. The two are split
deliberately: `build_daily_report()` takes a `Mapping[date, int]` rather than
a list of `ClosedTrade`, so the exact same function can score a live/paper
`TradeRow` day-mapping later without this module ever importing
`te.backtest.engine`'s trade type into that path.

Money stays in PAISE as `int` throughout this module — no floats, no
formatting. Rupee conversion happens only at a display boundary (a script or
API layer), never here.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from te.backtest.engine import ClosedTrade
from te.backtest.report import max_drawdown as _max_drawdown_paise
from te.backtest.strategy_lab import StrategyTrade
from te.domain.clock import IST
from te.domain.money import Paise

#: Rs 500 / Rs 1000, in paise — the two "did today clear a meaningful bar"
#: thresholds `DailyReport` counts against.
_RS_500_PAISE = 500_00
_RS_1000_PAISE = 1_000_00


def daily_net_from_trades(trades: Sequence[ClosedTrade]) -> dict[dt.date, int]:
    """Total net paise per calendar day, keyed by the IST date the trade
    CLOSED (`exit_ts` converted to `te.domain.clock.IST`, then `.date()`).

    A day with trades that net to exactly zero still appears as a key (the
    day happened); a day with no trades at all is simply absent — callers
    must not confuse "no key" with "netted to zero"."""
    daily: dict[dt.date, int] = {}
    for trade in trades:
        day = trade.exit_ts.astimezone(IST).date()
        daily[day] = daily.get(day, 0) + int(trade.net_pnl)
    return daily


def daily_net_from_strategy_trades(trades: Sequence[StrategyTrade]) -> dict[dt.date, int]:
    """Total net paise per calendar day, keyed by the IST date a
    `te.backtest.strategy_lab.StrategyTrade` CLOSED (`exit_ts`).

    The `StrategyTrade`-side counterpart to `daily_net_from_trades()` above.
    Kept as a separate function rather than widening that one, so this
    module never has to import `te.backtest.engine.ClosedTrade` and
    `te.backtest.strategy_lab.StrategyTrade` into the same code path merely
    to distinguish them — the two trade shapes are not interchangeable
    (`StrategyTrade` prices a real option premium; `ClosedTrade` is whatever
    its own producer priced), and this keeps that distinction structural,
    not just documented.

    Each trade contributes `net_paise_per_unit x lot_size x lots` — the real
    total rupees the position would have made or lost, not the per-unit
    figure alone."""
    daily: dict[dt.date, int] = {}
    for trade in trades:
        # `StrategyTrade.exit_ts` is typed optional only so older fixtures
        # that never set it stay constructible; `__post_init__` guarantees
        # it is populated (falling back to `entry_ts`) before anyone reads
        # it. The fallback here is for the type checker, not for runtime.
        close_ts = trade.exit_ts or trade.entry_ts
        day = close_ts.astimezone(IST).date()
        total = trade.net_paise_per_unit * trade.lot_size * trade.lots
        daily[day] = daily.get(day, 0) + total
    return daily


@dataclass(frozen=True)
class DailyReport:
    sessions: int  # days with at least one closed trade
    trades: int
    total_net_paise: int
    mean_daily_paise: int
    median_daily_paise: int
    best_day_paise: int
    worst_day_paise: int  # <= 0 when any losing day exists
    days_at_or_above_500: int
    days_at_or_above_1000: int
    losing_days: int
    longest_losing_streak: int  # consecutive losing SESSIONS
    max_drawdown_paise: int  # <= 0, from the daily equity curve
    max_drawdown_pct_of_capital: float  # positive magnitude, e.g. 12.5
    longest_days_underwater: int
    monthly_net_paise: dict[str, int]  # "YYYY-MM" -> paise


#: Per `honest-metrics`: at `sessions == 0` this is the ONLY thing
#: `build_daily_report()` may return — every field its honest zero-state,
#: never a plausible-looking default.
_ZERO_REPORT = DailyReport(
    sessions=0,
    trades=0,
    total_net_paise=0,
    mean_daily_paise=0,
    median_daily_paise=0,
    best_day_paise=0,
    worst_day_paise=0,
    days_at_or_above_500=0,
    days_at_or_above_1000=0,
    losing_days=0,
    longest_losing_streak=0,
    max_drawdown_paise=0,
    max_drawdown_pct_of_capital=0.0,
    longest_days_underwater=0,
    monthly_net_paise={},
)


def _round_paise(value: Decimal) -> int:
    """Round-half-up to the nearest whole paisa — deterministic, unlike
    float `round()`'s banker's-rounding-on-imprecise-binary behaviour at
    exact `.5` boundaries."""
    return int(value.quantize(Decimal(1), rounding=ROUND_HALF_UP))


def _median_paise(sorted_values: list[int]) -> int:
    n = len(sorted_values)
    mid = n // 2
    if n % 2 == 1:
        return sorted_values[mid]
    return _round_paise((Decimal(sorted_values[mid - 1]) + Decimal(sorted_values[mid])) / 2)


def _longest_streak(flags: list[bool]) -> int:
    longest = current = 0
    for flag in flags:
        if flag:
            current += 1
            longest = max(longest, current)
        else:
            current = 0
    return longest


def build_daily_report(
    daily_net_paise: Mapping[dt.date, int],
    *,
    capital: Paise,
    trade_count: int = 0,
) -> DailyReport:
    """Builds a `DailyReport` from a date -> net-paise mapping (see
    `daily_net_from_trades()` for the backtest-side producer of that
    mapping; a live/paper day-mapping is a valid input too).

    `trade_count` is the number of individual closed trades the mapping was
    aggregated FROM, when the caller has it (a day-net mapping alone cannot
    reveal how many trades netted to a given day's total — that granularity
    is lost by the aggregation this function's own input already performed).
    It is NOT derivable from `daily_net_paise` and is therefore a separate,
    optional, explicit parameter rather than something this function
    guesses at: omitted, it stays `0` — an honest "not supplied", never a
    plausible-looking count.

    Per `honest-metrics`: at zero sessions this returns the shared
    `_ZERO_REPORT` constant — every field genuinely zero, not a computed
    value that happens to look like one. No Sharpe is computed or exposed
    here at all; sample sizes in this project are too small for one, and
    `te.ml.metrics`/`te.backtest.scoring` already own the deflated score.
    """
    if not daily_net_paise:
        return _ZERO_REPORT

    days = sorted(daily_net_paise)
    nets = [daily_net_paise[d] for d in days]
    sessions = len(days)
    total = sum(nets)

    sorted_nets = sorted(nets)
    mean = _round_paise(Decimal(total) / Decimal(sessions))
    median = _median_paise(sorted_nets)

    losing_flags = [n < 0 for n in nets]
    losing_days = sum(losing_flags)
    longest_losing_streak = _longest_streak(losing_flags)

    # Equity curve = running cumulative sum of daily net, starting at
    # `capital` (portfolio-analytics's peak-to-trough convention). Peak-to-
    # trough magnitude is invariant to the curve's starting offset — a
    # constant shift moves every point (including the running peak) by the
    # same amount — so `report.max_drawdown()` (which builds its own curve
    # starting at 0) returns the identical drawdown paise figure either way.
    drawdown = _max_drawdown_paise(nets)
    capital_int = int(capital)
    drawdown_pct = (abs(drawdown) / capital_int * 100) if capital_int > 0 else 0.0

    equity_curve = []
    running = capital_int
    for n in nets:
        running += n
        equity_curve.append(running)
    peak = capital_int
    underwater_flags: list[bool] = []
    for value in equity_curve:
        peak = max(peak, value)
        underwater_flags.append(value < peak)
    longest_underwater = _longest_streak(underwater_flags)

    monthly: dict[str, int] = {}
    for day, net in zip(days, nets, strict=True):
        key = f"{day.year:04d}-{day.month:02d}"
        monthly[key] = monthly.get(key, 0) + net

    return DailyReport(
        sessions=sessions,
        trades=trade_count,
        total_net_paise=total,
        mean_daily_paise=mean,
        median_daily_paise=median,
        best_day_paise=max(nets),
        worst_day_paise=min(nets),
        days_at_or_above_500=sum(1 for n in nets if n >= _RS_500_PAISE),
        days_at_or_above_1000=sum(1 for n in nets if n >= _RS_1000_PAISE),
        losing_days=losing_days,
        longest_losing_streak=longest_losing_streak,
        max_drawdown_paise=drawdown,
        max_drawdown_pct_of_capital=drawdown_pct,
        longest_days_underwater=longest_underwater,
        monthly_net_paise=monthly,
    )
