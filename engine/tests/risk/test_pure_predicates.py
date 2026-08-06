"""`te.risk.limits`'s pure predicates — `daily_loss_breached`,
`consecutive_losses_breached`, `drawdown_breached`.

These exist so `te.backtest.strategy_lab.run_many` can enforce the exact same
rules the live `check_*` functions do, without a SQLAlchemy `Session` and
without a second implementation of the rule. Every case here is pinned
against the SAME boundary the corresponding `check_*` test in
`tests/risk/test_limits.py`/`tests/risk/test_consecutive_losses.py` proves —
this file is what shows the extraction did not change the boundary.
"""

from __future__ import annotations

from decimal import Decimal

from te.risk.limits import consecutive_losses_breached, daily_loss_breached, drawdown_breached

# --- daily_loss_breached --------------------------------------------------


def test_exactly_at_the_limit_breaches() -> None:
    """`net <= -limit` — AT the limit counts as breached, not just past it."""
    assert daily_loss_breached(net_paise=-4_000_00, max_daily_loss_paise=4_000_00) is True


def test_one_paise_short_of_the_limit_does_not_breach() -> None:
    assert daily_loss_breached(net_paise=-3_999_99, max_daily_loss_paise=4_000_00) is False


def test_one_paise_past_the_limit_breaches() -> None:
    assert daily_loss_breached(net_paise=-4_000_01, max_daily_loss_paise=4_000_00) is True


def test_a_positive_net_never_breaches() -> None:
    assert daily_loss_breached(net_paise=10_000_00, max_daily_loss_paise=4_000_00) is False


# --- consecutive_losses_breached ------------------------------------------


def test_streak_exactly_at_the_limit_breaches() -> None:
    assert consecutive_losses_breached(recent_trade_nets=[-500, -500, -500], limit=3) is True


def test_streak_one_short_of_the_limit_does_not_breach() -> None:
    assert consecutive_losses_breached(recent_trade_nets=[-500, -500], limit=3) is False


def test_a_winner_at_the_front_resets_the_streak() -> None:
    """`recent_trade_nets` is most-recent-first — a winner at index 0 must
    stop the count before it ever reaches the older losses."""
    assert consecutive_losses_breached(recent_trade_nets=[900, -500, -500, -500], limit=3) is False


def test_breakeven_at_the_front_counts_as_a_reset_not_a_loss() -> None:
    assert consecutive_losses_breached(recent_trade_nets=[0, -500, -500, -500], limit=3) is False


def test_zero_limit_disables_the_check_regardless_of_streak_length() -> None:
    assert consecutive_losses_breached(recent_trade_nets=[-500] * 10, limit=0) is False


def test_negative_limit_also_disables_the_check() -> None:
    assert consecutive_losses_breached(recent_trade_nets=[-500] * 10, limit=-1) is False


def test_empty_history_never_breaches() -> None:
    assert consecutive_losses_breached(recent_trade_nets=[], limit=1) is False


# --- drawdown_breached -----------------------------------------------------


def test_drawdown_exactly_at_the_limit_breaches() -> None:
    """10,000 peak, 9,000 equity is exactly a 10% drawdown."""
    assert (
        drawdown_breached(current_equity_paise=9_000_00, peak_equity_paise=10_000_00, max_drawdown_pct=Decimal(10))
        is True
    )


def test_drawdown_one_paise_inside_the_band_does_not_breach() -> None:
    assert (
        drawdown_breached(
            current_equity_paise=9_000_01, peak_equity_paise=10_000_00, max_drawdown_pct=Decimal(10)
        )
        is False
    )


def test_drawdown_past_the_limit_breaches() -> None:
    assert (
        drawdown_breached(current_equity_paise=8_500_00, peak_equity_paise=10_000_00, max_drawdown_pct=Decimal(10))
        is True
    )


def test_zero_or_negative_peak_never_breaches() -> None:
    """No meaningful watermark yet (e.g. capital itself is 0) — nothing to
    compare against, mirroring `check_max_drawdown`'s early return."""
    assert drawdown_breached(current_equity_paise=-500, peak_equity_paise=0, max_drawdown_pct=Decimal(10)) is False
    assert drawdown_breached(current_equity_paise=-500, peak_equity_paise=-100, max_drawdown_pct=Decimal(10)) is False


def test_equity_at_or_above_peak_never_breaches() -> None:
    assert (
        drawdown_breached(current_equity_paise=10_000_00, peak_equity_paise=10_000_00, max_drawdown_pct=Decimal(10))
        is False
    )
