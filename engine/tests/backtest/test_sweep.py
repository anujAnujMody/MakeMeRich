"""The sweep's two load-bearing shortcuts, and the reading aid that stops it
being read wrongly.

Sweeping 144 combinations naively means 144 walks over 636 sessions, roughly
twelve hours. `te.backtest.sweep` gets it to one walk by exploiting two facts:

1. A strategy's FIRINGS do not depend on stop, target, strike or risk — so
   history is walked once and the firing set is shared by every combination.
2. Risk-per-trade cannot change which barrier a premium path touches — it only
   changes affordability and lot count — so one labelling pass serves all four
   risk levels.

If either shortcut is wrong the whole sweep is wrong, quietly, in a way no
output would reveal. Hence these.

The third test guards the reading aid. Per `backtest-expert`, the single best
cell out of 144 is what noise looks like; the plateau view is what makes a
result believable, so its arithmetic has to be right.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest

from te.backtest.daily import build_daily_report
from te.backtest.strategy_lab import StrategyTrade
from te.backtest.sweep import SweepCell, plateau, replay
from te.domain.costs import CostModel
from te.domain.money import Paise

_ENTRY = dt.datetime(2026, 7, 31, 4, 30, tzinfo=dt.UTC)  # 10:00 IST


def _trade(
    *,
    minutes: int = 0,
    net_per_unit: int = 0,
    entry_premium: int = 7_400,
    stop: int = 6_660,
    lot_size: int = 65,
    hold_minutes: int = 5,
) -> StrategyTrade:
    """`hold_minutes` matters more than it looks. A trade's P&L does not
    reach equity until it RESOLVES, so a limit can only stop trades entered
    after enough losses have actually settled. Fixtures here hold for 5
    minutes and enter 10 apart, so each settles before the next fires —
    otherwise a test would be asserting that a limit stops a trade entered
    while the losing one is still open, which is precisely the lookahead
    this module was fixed to remove."""
    ts = _ENTRY + dt.timedelta(minutes=minutes)
    return StrategyTrade(
        entry_ts=ts,
        direction="long_call",
        option_symbol="NIFTY04AUG2624600CE",
        barrier="target" if net_per_unit > 0 else "stop",
        r_multiple=0.0,
        net_paise_per_unit=net_per_unit,
        lot_size=lot_size,
        exit_ts=ts + dt.timedelta(minutes=hold_minutes),
        entry_premium_paise=entry_premium,
        stop_premium_paise=stop,
        target_premium_paise=entry_premium + (entry_premium - stop),
    )


@pytest.fixture
def cost_model() -> CostModel:
    from te.data.charges_loader import load_charge_rate_table
    from te.domain.costs import select_rates
    from te.settings import Settings

    return CostModel(select_rates(load_charge_rate_table(Settings().charges_path), _ENTRY.date()))


def _replay(trades, **overrides):  # noqa: ANN001, ANN003, ANN202
    from te.data.charges_loader import load_charge_rate_table
    from te.domain.costs import select_rates
    from te.settings import Settings

    base = {
        "capital": Paise(3_000_000),  # Rs 30,000
        "risk_budget_pct": Decimal(5),
        "max_position_size_pct": Decimal(25),
        "min_edge_multiple": Decimal(0),
        "cost_model": CostModel(select_rates(load_charge_rate_table(Settings().charges_path), _ENTRY.date())),
        "exchange": "NFO",
        "max_daily_loss_paise": None,
        "max_consecutive_losses": None,
        "max_drawdown_pct": None,
        "compound_equity": False,
    }
    return replay(trades, **{**base, **overrides})


def test_the_daily_loss_limit_stops_taking_trades_that_day(cost_model: CostModel) -> None:
    """The limit is the whole reason a rupee report differs from an
    R-multiple one: it caps how bad a day can get, so a strategy cannot be
    credited with losses it would never have been allowed to take."""
    losers = [_trade(minutes=i * 10, net_per_unit=-740) for i in range(6)]

    unlimited = _replay(losers)
    limited = _replay(losers, max_daily_loss_paise=100_000)  # Rs 1,000

    assert limited.trades < unlimited.trades, "the limit never stopped anything"
    assert limited.halted_days == 1
    assert sum(limited.daily_net_paise.values()) > sum(unlimited.daily_net_paise.values())


def test_a_standdown_resets_the_next_day(cost_model: CostModel) -> None:
    """Mirrors live (`check_consecutive_losses` resets daily). A standdown
    that carried over would silently shrink every later day's sample."""
    day_one = [_trade(minutes=i * 10, net_per_unit=-740) for i in range(4)]
    day_two = [_trade(minutes=24 * 60 + i * 10, net_per_unit=-740) for i in range(4)]

    result = _replay(day_one + day_two, max_consecutive_losses=3)

    assert result.standdown_days == 2, "the second day did not get its own fresh allowance"


def test_a_bigger_risk_budget_takes_trades_a_smaller_one_cannot_afford() -> None:
    """The premise of re-sizing rather than re-labelling: risk % changes only
    affordability and lot count, never the outcome. At Rs 30,000 with a 10%
    stop, one NIFTY lot risks about Rs 480 — over a 1% budget (Rs 300) and
    under a 5% one (Rs 1,500)."""
    trades = [_trade(minutes=0, net_per_unit=740)]

    tight = _replay(trades, risk_budget_pct=Decimal(1))
    loose = _replay(trades, risk_budget_pct=Decimal(5))

    assert tight.trades == 0
    assert tight.unaffordable == 1, "a rejected trade must be counted, never silently dropped"
    assert loose.trades == 1


def test_an_unaffordable_trade_is_never_counted_as_a_trade() -> None:
    """Per `honest-metrics`: on small capital most signals are unaffordable,
    and reporting only the ones that fit would overstate how much the
    strategy actually traded."""
    result = _replay([_trade(net_per_unit=740, entry_premium=100_000, stop=90_000)], risk_budget_pct=Decimal(1))

    assert result.trades == 0
    assert result.unaffordable == 1
    assert result.daily_net_paise == {}


def test_compounding_shrinks_position_size_as_the_account_drains() -> None:
    """Without this the backtest keeps betting the original stake while the
    account is being wiped out, which overstates the loss.

    Needs enough losses to actually bite: one NIFTY lot costs about Rs 4,810,
    and the 25%-of-capital position cap only drops below that once equity
    falls under roughly Rs 19,240 — about 23 losing trades from Rs 30,000.
    A shorter sequence would pass identically either way and prove nothing."""
    losers = [_trade(minutes=i * 10, net_per_unit=-740) for i in range(30)]

    flat = _replay(losers, risk_budget_pct=Decimal(5), compound_equity=False)
    compounded = _replay(losers, risk_budget_pct=Decimal(5), compound_equity=True)

    assert compounded.final_equity_paise > flat.final_equity_paise


def test_the_drawdown_breaker_stops_the_run_for_good() -> None:
    """Unlike the daily limit, this one does not reset tomorrow — matching
    live, where it is a persistent halt requiring a human."""
    losers = [_trade(minutes=i * 600, net_per_unit=-740) for i in range(40)]

    result = _replay(losers, risk_budget_pct=Decimal(5), max_drawdown_pct=Decimal(20), compound_equity=True)

    assert result.drawdown_halted is True
    assert result.trades < 40


def test_the_plateau_averages_over_every_other_parameter() -> None:
    """The reading aid that decides whether a result is believed. If 1%, 2%
    and 3% risk all land in the same place that is a finding; if only one
    value works, that is noise. Getting this arithmetic wrong would make
    noise look like a plateau."""

    def cell(risk: int, stop: int, mean: int) -> SweepCell:
        return SweepCell(
            strategy="orb",
            risk_pct=Decimal(risk),
            stop_pct=Decimal(stop),
            target_pct=Decimal(20),
            strikes_out_of_the_money=0,
            report=build_daily_report({dt.date(2026, 7, 31): mean}, capital=Paise(3_000_000)),
            trades=1,
            unaffordable=0,
            halted_days=0,
            standdown_days=0,
            drawdown_halted=False,
            final_equity_paise=3_000_000,
        )

    cells = [cell(1, 8, 100), cell(1, 10, 300), cell(2, 8, -50), cell(2, 10, -150)]

    by_risk = {str(v): mean for v, mean, _ in plateau(cells, strategy="orb", parameter="risk_pct")}

    assert by_risk["1"] == 200, "risk=1 should average its two stop values (100, 300)"
    assert by_risk["2"] == -100
    assert all(n == 2 for _, _, n in plateau(cells, strategy="orb", parameter="risk_pct"))


def test_the_plateau_ignores_the_other_strategy() -> None:
    """`random_entry` runs alongside as the control. Averaging the two
    together would hide exactly the difference the sweep exists to find."""

    def cell(strategy: str, mean: int) -> SweepCell:
        return SweepCell(
            strategy=strategy,
            risk_pct=Decimal(1),
            stop_pct=Decimal(10),
            target_pct=Decimal(20),
            strikes_out_of_the_money=0,
            report=build_daily_report({dt.date(2026, 7, 31): mean}, capital=Paise(3_000_000)),
            trades=1,
            unaffordable=0,
            halted_days=0,
            standdown_days=0,
            drawdown_halted=False,
            final_equity_paise=3_000_000,
        )

    cells = [cell("orb", 500), cell("random_entry", -500)]

    assert plateau(cells, strategy="orb", parameter="risk_pct") == [(Decimal(1), 500, 1)]


def test_trades_are_replayed_in_time_order_whatever_order_they_arrive_in() -> None:
    """Equity, the drawdown watermark and the daily buckets are all
    sequential. Labelling does not guarantee ordering, so the replay must
    impose it — out of order, the drawdown breaker would fire against a peak
    that had not happened yet."""
    trades = [_trade(minutes=30, net_per_unit=740), _trade(minutes=0, net_per_unit=-740)]

    shuffled = _replay(trades)
    ordered = _replay(sorted(trades, key=lambda t: t.entry_ts))

    assert shuffled.final_equity_paise == ordered.final_equity_paise
    assert shuffled.daily_net_paise == ordered.daily_net_paise


def test_the_replay_reads_no_bars_at_all() -> None:
    """The premise that makes four risk levels nearly free. Every premium
    `size_position` needs was carried onto the trade at labelling time, so a
    replay must work with no `BarStore` in sight — as this test does, by
    never providing one."""
    result = _replay([_trade(net_per_unit=740)], risk_budget_pct=Decimal(5))

    assert result.trades == 1
