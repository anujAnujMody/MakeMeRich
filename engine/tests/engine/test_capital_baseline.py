"""Changing capital must re-base the account, not stack onto the old one.

Fixed 2026-08-05, after the owner raised capital from Rs 30,000 to
Rs 50,000. Equity is `capital + realized P&L`, and "realized P&L" had no
anchor — it meant LIFETIME P&L across every closed trade ever. So the new
Rs 50,000 balance was immediately credited with Rs 11,837 of paper profit
earned back when the account was Rs 30,000, and the next trade would have
been sized off Rs 61,837: a balance that never existed under either
setting.

Two stored numbers depend on the base, and both are asserted here:

* the sizing/drawdown equity, via `capital_set_at`
* `check_max_drawdown`'s peak-equity watermark, which otherwise keeps the
  old P&L in it and reads the config change as an instant drawdown

The `since` filter is on `closed_at` and that is load-bearing: `net_pnl`
only exists at close, so a trade open across the change belongs to the
period it settled in.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from pathlib import Path

import pytest

from te.domain.money import Paise
from te.engine.state import (
    AccountGuardrails,
    get_capital_set_at,
    get_peak_equity_paise,
    set_guardrails,
)
from te.persistence.db import make_engine, make_session_factory
from te.persistence.models import Base, TradeRow
from te.persistence.repos.paper_trading import total_net_pnl_paise
from te.risk.limits import RiskLimitsConfig, check_max_drawdown


@pytest.fixture
def session_factory(tmp_path: Path):  # noqa: ANN201
    engine = make_engine(f"sqlite:///{tmp_path / 'capital_baseline.db'}")
    Base.metadata.create_all(engine)
    return make_session_factory(engine)


def _guardrails(capital_rupees: int) -> AccountGuardrails:
    return AccountGuardrails(
        capital=Paise(capital_rupees * 100),
        max_daily_loss=Paise(2_000_00),
        max_position_size_pct=Decimal(50),
        max_drawdown_pct=Decimal(20),
        max_trades_per_day=20,
        max_concurrent_positions=5,
        risk_per_trade_pct=Decimal(3),
    )


def _closed_trade(session, *, net_paise: int, closed_at: dt.datetime, tag: str) -> None:  # noqa: ANN001
    session.add(
        TradeRow(
            client_order_id=tag,
            symbol="NIFTY30JUN2626500CE",
            exchange="NFO",
            strategy="orb",
            direction="long_call",
            lots=1,
            lot_size=65,
            entry_premium_paise=10_000,
            exit_premium_paise=10_000 + (net_paise // 65),
            gross_pnl_paise=net_paise,
            costs_paise=0,
            net_pnl_paise=net_paise,
            exit_reason="target",
            mode="paper",
            opened_at=closed_at - dt.timedelta(hours=1),
            closed_at=closed_at,
            stop_paise=8_000,
            target_paise=12_000,
        )
    )


BEFORE = dt.datetime(2026, 8, 4, 10, 0, tzinfo=dt.UTC)
NOW = dt.datetime(2026, 8, 5, 10, 0, tzinfo=dt.UTC)


def test_profit_earned_under_the_old_capital_does_not_fund_the_new_one(session_factory) -> None:  # noqa: ANN001
    """The exact 2026-08-05 case, in miniature."""
    with session_factory() as session:
        _closed_trade(session, net_paise=11_837_00, closed_at=BEFORE, tag="old-profit")
        set_guardrails(session, _guardrails(30_000))
        session.commit()

    with session_factory() as session:
        set_guardrails(session, _guardrails(50_000))
        session.commit()

    with session_factory() as session:
        anchor = get_capital_set_at(session)
        assert anchor is not None, "changing capital must stamp an anchor"
        assert total_net_pnl_paise(session, since=anchor) == Paise(0)
        # Unanchored, the old behaviour — kept working, and kept wrong for
        # this purpose. Asserted so the two readings are visibly different.
        assert total_net_pnl_paise(session) == Paise(11_837_00)


def test_pnl_after_the_change_still_counts(session_factory) -> None:  # noqa: ANN001
    """The anchor must not freeze equity — only exclude what predates it."""
    with session_factory() as session:
        set_guardrails(session, _guardrails(30_000))
        session.commit()
    with session_factory() as session:
        set_guardrails(session, _guardrails(50_000))
        session.commit()
    with session_factory() as session:
        anchor = get_capital_set_at(session)
        assert anchor is not None
        _closed_trade(session, net_paise=-1_500_00, closed_at=anchor + dt.timedelta(minutes=5), tag="new-loss")
        session.commit()

    with session_factory() as session:
        assert total_net_pnl_paise(session, since=get_capital_set_at(session)) == Paise(-1_500_00)


def test_re_saving_the_same_capital_does_not_move_the_anchor(session_factory) -> None:  # noqa: ANN001
    """Editing the daily-loss limit is not a restatement of what the
    account is worth. If it moved the anchor, every unrelated settings save
    would silently erase the running P&L the drawdown breaker depends on."""
    with session_factory() as session:
        set_guardrails(session, _guardrails(30_000))
        session.commit()
    with session_factory() as session:
        set_guardrails(session, _guardrails(50_000))
        session.commit()
    with session_factory() as session:
        first = get_capital_set_at(session)

    with session_factory() as session:
        same_capital = _guardrails(50_000)
        set_guardrails(session, AccountGuardrails(**{**same_capital.__dict__, "max_daily_loss": Paise(1_000_00)}))
        session.commit()

    with session_factory() as session:
        assert get_capital_set_at(session) == first


def test_never_changing_capital_keeps_lifetime_pnl(session_factory) -> None:  # noqa: ANN001
    """`None` is a real answer, not a missing value: an account whose
    capital has never been CHANGED has no re-basing event, so all of its
    history counts. The first-ever save is not a change either — with no
    stored value there is no previous account to separate the new one
    from."""
    with session_factory() as session:
        _closed_trade(session, net_paise=500_00, closed_at=BEFORE, tag="only-trade")
        session.commit()
    with session_factory() as session:
        assert get_capital_set_at(session) is None
        assert total_net_pnl_paise(session, since=None) == Paise(500_00)


def test_a_capital_change_clears_the_drawdown_watermark(session_factory) -> None:  # noqa: ANN001
    """Otherwise the stored peak still carries the old P&L, equity restarts
    without it, and the difference reads as a drawdown the account never
    took — halting the engine on a config edit.

    CLEARED rather than rewritten, and the next test says why."""
    with session_factory() as session:
        _closed_trade(session, net_paise=11_837_00, closed_at=BEFORE, tag="old-profit")
        set_guardrails(session, _guardrails(30_000))
        session.commit()

    with session_factory() as session:
        set_guardrails(session, _guardrails(50_000))
        session.commit()

    with session_factory() as session:
        assert get_peak_equity_paise(session) is None
        # Realized P&L restarts at the anchor, so the drawdown breaker will
        # seed its watermark from real equity on the next cycle.
        assert total_net_pnl_paise(session, since=get_capital_set_at(session)) == Paise(0)


def test_a_capital_change_with_an_open_LOSING_position_does_not_halt(session_factory) -> None:  # noqa: N802, ANN001
    """The regression this fix exists for, asserted end to end through the
    REAL breaker rather than by inspecting a stored number.

    The first version of the re-base wrote `peak = capital`. Equity is
    `capital + realized-since-anchor + unrealized`, and right after a
    re-base the realized term is 0 by construction — but the unrealized
    term is not. So an operator raising capital while a position was
    Rs 10,000 underwater got equity of Rs 40,000 against a fresh Rs 50,000
    watermark: a 20% drawdown, an instant halt, and a manual clear, from a
    config edit that lost nothing.

    This is the exact failure the code that rule REPLACED was written to
    prevent, reintroduced through a different door — which is why the
    assertion here is "the breaker does not fire", not "the number is X".
    """
    with session_factory() as session:
        set_guardrails(session, _guardrails(30_000))
        session.commit()
    with session_factory() as session:
        set_guardrails(session, _guardrails(50_000))
        session.commit()

    underwater = Paise(40_000_00)  # Rs 50,000 capital, Rs 10,000 unrealized loss
    limits = RiskLimitsConfig(
        max_daily_loss_paise=Paise(2_000_00),
        max_concurrent_positions=5,
        max_trades_per_day=20,
        max_drawdown_pct=Decimal(20),
    )
    with session_factory() as session:
        # Must not raise. With a stale `peak = capital` this is a 20% breach.
        check_max_drawdown(session, limits, now=NOW, current_equity_paise=underwater)
        session.commit()

    with session_factory() as session:
        # And the watermark it seeded is REAL equity, not the configured
        # capital — so the next tick measures drawdown from where the
        # account actually was.
        assert get_peak_equity_paise(session) == underwater
