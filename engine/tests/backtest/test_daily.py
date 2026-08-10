"""`te.backtest.daily` — rupees-per-day reporting built on top of
`te.backtest.engine.ClosedTrade.net_pnl` (never gross), reusing
`te.backtest.report.max_drawdown`'s peak-to-trough formula rather than a
second one."""

from __future__ import annotations

import datetime as dt

import pytest

from te.backtest.daily import DailyReport, build_daily_report, daily_net_from_strategy_trades, daily_net_from_trades
from te.backtest.engine import ClosedTrade
from te.backtest.strategy_lab import StrategyTrade
from te.domain.costs import CostBreakdown
from te.domain.money import Paise
from te.domain.pnl import GrossPnl, NetPnl

#: Deliberately non-zero, following `tests/backtest/test_report.py::_trade`'s
#: pattern: a `_COSTS` of zero (as this fixture previously used) makes
#: `gross_pnl` numerically equal to `net_pnl`, so a mutation that reads
#: `gross_pnl` instead of `net_pnl` (`te/backtest/daily.py:47`) would pass
#: every test in this file undetected.
_COSTS = CostBreakdown(
    brokerage=Paise(200), stt=Paise(150), exchange_txn=Paise(50), sebi=Paise(1), gst=Paise(80), stamp=Paise(6)
)


def _trade(*, exit_ts: dt.datetime, net_pnl: int) -> ClosedTrade:
    # `gross_pnl` deliberately DIFFERS from `net_pnl` by the real cost total
    # (`gross == net + costs`) rather than being set equal to it, so a
    # gross-instead-of-net mutation changes the aggregated total.
    gross_pnl = net_pnl + _COSTS.total
    return ClosedTrade(
        symbol="NIFTY", exchange="NFO", strategy="orb", direction="long_call", lots=1, lot_size=65,
        entry_premium=Paise(10_000), exit_premium=Paise(10_500), entry_ts=exit_ts, exit_ts=exit_ts,
        exit_reason="target", gross_pnl=GrossPnl(Paise(gross_pnl)), costs=_COSTS,
        net_pnl=NetPnl(Paise(net_pnl)),
    )


# -- daily_net_from_trades ---------------------------------------------------


def test_daily_net_from_trades_groups_by_ist_close_date_and_sums() -> None:
    """Two trades closing the same IST day must net together into one key,
    not overwrite each other."""
    day1_morning = dt.datetime(2026, 7, 29, 4, 0, tzinfo=dt.UTC)  # 09:30 IST
    day1_afternoon = dt.datetime(2026, 7, 29, 9, 0, tzinfo=dt.UTC)  # 14:30 IST
    trades = [
        _trade(exit_ts=day1_morning, net_pnl=1_000),
        _trade(exit_ts=day1_afternoon, net_pnl=-300),
    ]
    daily = daily_net_from_trades(trades)
    assert daily == {dt.date(2026, 7, 29): 700}


def test_daily_net_from_trades_a_trade_closing_just_after_ist_midnight_lands_on_the_correct_day() -> None:
    """00:05 IST is 18:35 UTC the PREVIOUS day — a naive UTC `.date()` would
    misfile this trade onto 2026-07-29 instead of 2026-07-30. Guards against
    exactly that UTC/IST drift."""
    just_after_midnight_ist = dt.datetime(2026, 7, 29, 18, 35, tzinfo=dt.UTC)
    trade = _trade(exit_ts=just_after_midnight_ist, net_pnl=500)
    daily = daily_net_from_trades([trade])
    assert daily == {dt.date(2026, 7, 30): 500}


def test_daily_net_from_trades_empty_input_is_empty_dict() -> None:
    assert daily_net_from_trades([]) == {}


def test_daily_net_from_trades_a_day_with_no_trades_is_absent_not_zero() -> None:
    trades = [_trade(exit_ts=dt.datetime(2026, 7, 29, 4, 0, tzinfo=dt.UTC), net_pnl=100)]
    daily = daily_net_from_trades(trades)
    assert dt.date(2026, 7, 30) not in daily


# -- daily_net_from_strategy_trades (the real-option-premium producer) ------


def _strategy_trade(*, exit_ts: dt.datetime, net_paise_per_unit: int, lots: int, lot_size: int) -> StrategyTrade:
    return StrategyTrade(
        entry_ts=exit_ts,
        direction="long_call",
        option_symbol="NIFTY10MAR26C24000",
        barrier="target" if net_paise_per_unit > 0 else "stop",
        r_multiple=1.0 if net_paise_per_unit > 0 else -1.0,
        net_paise_per_unit=net_paise_per_unit,
        lots=lots,
        lot_size=lot_size,
        exit_ts=exit_ts,
    )


def test_daily_net_from_strategy_trades_groups_by_ist_close_date_and_sums_total_rupees() -> None:
    """Each trade contributes `net_paise_per_unit x lot_size x lots` — the
    real total, not the per-unit figure alone."""
    day1_morning = dt.datetime(2026, 7, 29, 4, 0, tzinfo=dt.UTC)  # 09:30 IST
    day1_afternoon = dt.datetime(2026, 7, 29, 9, 0, tzinfo=dt.UTC)  # 14:30 IST
    trades = [
        _strategy_trade(exit_ts=day1_morning, net_paise_per_unit=50, lots=2, lot_size=75),  # 50*2*75 = 7,500
        _strategy_trade(exit_ts=day1_afternoon, net_paise_per_unit=-20, lots=1, lot_size=75),  # -20*1*75 = -1,500
    ]
    daily = daily_net_from_strategy_trades(trades)
    assert daily == {dt.date(2026, 7, 29): 6_000}


def test_daily_net_from_strategy_trades_keys_on_exit_ts_not_entry_ts() -> None:
    """`StrategyTrade` keeps `entry_ts` and `exit_ts` distinct; the daily
    mapping must key on when the position CLOSED, matching
    `daily_net_from_trades`'s own convention."""
    entry = dt.datetime(2026, 7, 29, 9, 0, tzinfo=dt.UTC)  # 14:30 IST
    exit_ts = dt.datetime(2026, 7, 30, 5, 0, tzinfo=dt.UTC)  # next-day 10:30 IST
    trade = StrategyTrade(
        entry_ts=entry, direction="long_call", option_symbol="NIFTY10MAR26C24000",
        barrier="target", r_multiple=1.0, net_paise_per_unit=100, lots=1, lot_size=75, exit_ts=exit_ts,
    )
    daily = daily_net_from_strategy_trades([trade])
    assert daily == {dt.date(2026, 7, 30): 7_500}
    assert dt.date(2026, 7, 29) not in daily


def test_daily_net_from_strategy_trades_a_trade_closing_just_after_ist_midnight_lands_on_the_correct_day() -> None:
    just_after_midnight_ist = dt.datetime(2026, 7, 29, 18, 35, tzinfo=dt.UTC)  # 00:05 IST, next day
    trade = _strategy_trade(exit_ts=just_after_midnight_ist, net_paise_per_unit=100, lots=1, lot_size=75)
    daily = daily_net_from_strategy_trades([trade])
    assert daily == {dt.date(2026, 7, 30): 7_500}


def test_daily_net_from_strategy_trades_empty_input_is_empty_dict() -> None:
    assert daily_net_from_strategy_trades([]) == {}


# -- build_daily_report -------------------------------------------------------


def _days(*offsets_and_nets: tuple[int, int]) -> dict[dt.date, int]:
    base = dt.date(2026, 7, 1)
    return {base + dt.timedelta(days=offset): net for offset, net in offsets_and_nets}


def test_build_daily_report_on_empty_mapping_is_the_honest_zero_state() -> None:
    """No fabricated numbers at zero sessions — every field is genuinely
    zero/empty, per `honest-metrics`."""
    report = build_daily_report({}, capital=Paise(30_000_00))
    assert report == DailyReport(
        sessions=0, trades=0, total_net_paise=0, mean_daily_paise=0, median_daily_paise=0,
        best_day_paise=0, worst_day_paise=0, days_at_or_above_500=0, days_at_or_above_1000=0,
        losing_days=0, longest_losing_streak=0, max_drawdown_paise=0, max_drawdown_pct_of_capital=0.0,
        longest_days_underwater=0, monthly_net_paise={},
    )


def test_build_daily_report_hand_computed_mean_median_best_worst_and_thresholds() -> None:
    """Hand-built sequence, in rupees for readability then converted to
    paise: +200, +600, -100, +1200, -50, +500, +900, +1000.

    The last day is exactly the `days_at_or_above_1000` boundary (Rs 1,000)
    -- `_RS_500_PAISE`'s boundary is already exercised by the Rs 500 day
    above it, but a `>` vs `>=` mutation on the 1,000 threshold
    (`te/backtest/daily.py:224`) previously had no day at exactly that
    value to catch it.

    sorted: [-100, -50, 200, 500, 600, 900, 1000, 1200] -> median = (500+600)/2 = 550
    mean = (200+600-100+1200-50+500+900+1000)/8 = 4250/8 = 531.25 Rs
    best = 1200, worst = -100
    >= 500: 500, 600, 900, 1000, 1200 -> 5 days
    >= 1000: 1000, 1200 -> 2 days
    losing days: -100, -50 -> 2
    """
    rupee_nets = [200, 600, -100, 1200, -50, 500, 900, 1000]
    daily = _days(*enumerate(n * 100 for n in rupee_nets))
    report = build_daily_report(daily, capital=Paise(30_000_00), trade_count=10)

    assert report.sessions == 8
    assert report.trades == 10
    assert report.total_net_paise == sum(n * 100 for n in rupee_nets)
    assert report.mean_daily_paise == 53_125  # 425000/8 = 53125 exactly
    assert report.median_daily_paise == 550_00
    assert report.best_day_paise == 1_200_00
    assert report.worst_day_paise == -100_00
    assert report.days_at_or_above_500 == 5
    assert report.days_at_or_above_1000 == 2
    assert report.losing_days == 2


def test_build_daily_report_median_of_an_even_count_averages_the_two_middle_values() -> None:
    rupee_nets = [100, 300, -200, 400]  # sorted: -200, 100, 300, 400 -> median (100+300)/2 = 200
    daily = _days(*enumerate(n * 100 for n in rupee_nets))
    report = build_daily_report(daily, capital=Paise(30_000_00))
    assert report.median_daily_paise == 200_00


def test_build_daily_report_longest_losing_streak_across_two_separate_runs() -> None:
    """Sequence (Rs): +100, -50, -60, -10, +200, -30, -40, +500.
    Two losing runs: length 3 (days 2-4) and length 2 (days 6-7). The
    longest is 3, and it must not be conflated with the total losing-day
    count (5) or bridged across the winning day in between."""
    rupee_nets = [100, -50, -60, -10, 200, -30, -40, 500]
    daily = _days(*enumerate(n * 100 for n in rupee_nets))
    report = build_daily_report(daily, capital=Paise(30_000_00))
    assert report.losing_days == 5
    assert report.longest_losing_streak == 3


def test_build_daily_report_max_drawdown_matches_hand_computed_curve_and_pct_of_capital() -> None:
    """Capital 10,000 Rs. Daily nets (Rs): +100, +200, -800, +50.
    Equity curve: 10100, 10300, 9500, 9550.
    Peak before the drop is 10300; trough is 9500 -> drawdown = -800 Rs =
    -80,000 paise. pct of capital = 80000 / 1_000_000 * 100 = 8.0."""
    rupee_nets = [100, 200, -800, 50]
    daily = _days(*enumerate(n * 100 for n in rupee_nets))
    capital = Paise(10_000_00)
    report = build_daily_report(daily, capital=capital)
    assert report.max_drawdown_paise == -80_000
    assert report.max_drawdown_pct_of_capital == pytest.approx(8.0)


def test_build_daily_report_longest_days_underwater_counts_consecutive_below_peak_sessions() -> None:
    """Same curve as the drawdown test: 10100 (new peak), 10300 (new peak),
    9500 (underwater), 9550 (still underwater, hasn't reclaimed 10300) ->
    two consecutive underwater sessions."""
    rupee_nets = [100, 200, -800, 50]
    daily = _days(*enumerate(n * 100 for n in rupee_nets))
    report = build_daily_report(daily, capital=Paise(10_000_00))
    assert report.longest_days_underwater == 2


def test_build_daily_report_worst_day_is_non_positive_when_a_losing_day_exists() -> None:
    daily = _days((0, 500_00), (1, -100_00))
    report = build_daily_report(daily, capital=Paise(30_000_00))
    assert report.worst_day_paise <= 0


def test_build_daily_report_monthly_net_paise_groups_by_calendar_month() -> None:
    daily = {
        dt.date(2026, 6, 29): 100_00,
        dt.date(2026, 6, 30): 200_00,
        dt.date(2026, 7, 1): -50_00,
    }
    report = build_daily_report(daily, capital=Paise(30_000_00))
    assert report.monthly_net_paise == {"2026-06": 300_00, "2026-07": -50_00}


def test_build_daily_report_sorts_by_date_before_computing_streaks_even_if_input_order_is_scrambled() -> None:
    """A `dict` built out of order must not corrupt the streak/curve
    computation — `build_daily_report` is documented to sort by date first."""
    scrambled = {
        dt.date(2026, 7, 3): -60_00,
        dt.date(2026, 7, 1): 100_00,
        dt.date(2026, 7, 2): -50_00,
        dt.date(2026, 7, 4): 200_00,
    }
    report = build_daily_report(scrambled, capital=Paise(30_000_00))
    assert report.longest_losing_streak == 2
