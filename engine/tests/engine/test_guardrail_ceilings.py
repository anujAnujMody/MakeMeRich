"""Hard server-side ceilings on the dashboard-editable risk guardrails
(Phase 0 item 1 of `docs/FableImprovements.md`).

The values actually in the live DB on 2026-07-31 were every one of these
fields at or above its ceiling — 5% risk per trade, a Rs 45,000 daily loss
limit on Rs 3,00,000 capital, and both the drawdown and position-notional
caps at 100%, i.e. disabled. The Settings page's own `(0, 100]` validation
accepted all of it.

Two directions are tested because both are load-bearing: rejection on write
tells the operator the limit exists, and clamping on read is what makes
values ALREADY stored above a ceiling stop being honoured without a
migration.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from te.domain.money import Paise
from te.engine.state import (
    MAX_DAILY_LOSS_PCT_OF_CAPITAL,
    MAX_DRAWDOWN_PCT_CEILING,
    MAX_POSITION_SIZE_PCT_CEILING,
    MAX_RISK_PER_TRADE_PCT,
    AccountGuardrails,
    get_guardrails,
    set_guardrails,
    upsert_engine_state,
)
from te.persistence.db import make_engine, make_session_factory, session_scope
from te.persistence.models import Base

CAPITAL = Paise(30_000_000)  # Rs 3,00,000 — the real live value


def _ceiling_daily_loss(capital: Paise) -> Paise:
    """The permitted daily loss at `capital`, derived from the constant.

    Written this way on purpose: the ceiling moved once already (5% -> 7% on
    2026-08-05) and every hardcoded rupee expectation in this file silently
    became an assertion about a value BELOW the limit rather than ON it —
    which is the one thing a ceiling test must not do."""
    return Paise(int(Decimal(int(capital)) * MAX_DAILY_LOSS_PCT_OF_CAPITAL / Decimal(100)))


@pytest.fixture
def session_factory(tmp_path: Path):  # noqa: ANN201
    engine = make_engine(f"sqlite:///{tmp_path / 'guardrails.db'}")
    Base.metadata.create_all(engine)
    return make_session_factory(engine)


def _safe(**overrides: object) -> AccountGuardrails:
    base = {
        "capital": CAPITAL,
        "max_daily_loss": Paise(1_000_000),  # Rs 10,000 = 3.3% of capital
        "max_position_size_pct": Decimal(20),
        "max_drawdown_pct": Decimal(15),
        "max_trades_per_day": 20,
        "max_concurrent_positions": 5,
        "risk_per_trade_pct": Decimal(1),
    }
    base.update(overrides)
    return AccountGuardrails(**base)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("field", "value", "needle"),
    [
        ("risk_per_trade_pct", MAX_RISK_PER_TRADE_PCT + Decimal(1), "exceeds the hard ceiling"),
        ("max_position_size_pct", Decimal(100), "exceeds the hard ceiling"),
        ("max_drawdown_pct", Decimal(100), "never trips"),
        ("max_daily_loss", Paise(4_500_000), "of capital"),  # Rs 45,000 = 15%
    ],
)
def test_saving_above_a_ceiling_is_rejected(session_factory, field, value, needle) -> None:  # noqa: ANN001
    with session_scope(session_factory) as session, pytest.raises(ValueError, match=needle):
        set_guardrails(session, _safe(**{field: value}))


def test_a_save_at_the_ceiling_is_allowed(session_factory) -> None:
    """The ceiling is inclusive — it is a limit, not an open interval, and an
    operator deliberately choosing the maximum must not be refused."""
    with session_scope(session_factory) as session:
        set_guardrails(
            session,
            _safe(
                risk_per_trade_pct=MAX_RISK_PER_TRADE_PCT,
                max_position_size_pct=MAX_POSITION_SIZE_PCT_CEILING,
                max_drawdown_pct=MAX_DRAWDOWN_PCT_CEILING,
                # Exactly the ceiling — derived from the constant rather than
                # written as a literal, so raising the ceiling (as happened on
                # 2026-08-05, 5% -> 7%) cannot leave this test asserting a
                # value that is merely BELOW the limit it claims to sit on.
                max_daily_loss=_ceiling_daily_loss(CAPITAL),
            ),
        )


def test_values_already_stored_above_a_ceiling_are_clamped_on_read(session_factory) -> None:
    """The exact live state on 2026-07-31, written straight to the key/value
    table the way it got there — bypassing `set_guardrails`, which did not
    validate these fields when those rows were written. Reading must return
    the ceilings, so the trading loop cannot honour them for even one cycle.
    """
    with session_scope(session_factory) as session:
        upsert_engine_state(session, "capital_paise", "30000000")
        upsert_engine_state(session, "risk_per_trade_pct", "5.0")
        upsert_engine_state(session, "max_daily_loss_paise", "4500000")
        upsert_engine_state(session, "max_drawdown_pct", "100.0")
        upsert_engine_state(session, "max_position_size_pct", "100.0")

    with session_factory() as session:
        got = get_guardrails(session, defaults=_safe())

    assert got.risk_per_trade_pct == MAX_RISK_PER_TRADE_PCT
    assert got.max_drawdown_pct == MAX_DRAWDOWN_PCT_CEILING
    assert got.max_position_size_pct == MAX_POSITION_SIZE_PCT_CEILING
    assert got.max_daily_loss == _ceiling_daily_loss(CAPITAL), (
        f"Rs 45,000 on Rs 3,00,000 capital is 15%, ceiling is {MAX_DAILY_LOSS_PCT_OF_CAPITAL}%"
    )


def test_clamping_never_raises_a_value(session_factory) -> None:
    """Clamping is one-directional. An operator who chose something SAFER
    than the ceiling keeps their choice — the ceiling is a maximum, not a
    target to snap to."""
    with session_scope(session_factory) as session:
        set_guardrails(session, _safe(risk_per_trade_pct=Decimal("0.5"), max_drawdown_pct=Decimal(5)))

    with session_factory() as session:
        got = get_guardrails(session, defaults=_safe())

    assert got.risk_per_trade_pct == Decimal("0.5")
    assert got.max_drawdown_pct == Decimal(5)


def test_the_daily_loss_ceiling_scales_with_capital(session_factory) -> None:
    """It is a PERCENT OF CAPITAL, not a fixed rupee figure — so raising
    capital raises the permitted daily loss, and lowering capital lowers it
    without needing the operator to re-save."""
    smaller = Paise(10_000_000)  # Rs 1,00,000
    with session_scope(session_factory) as session:
        upsert_engine_state(session, "capital_paise", str(int(smaller)))
        upsert_engine_state(session, "max_daily_loss_paise", "4500000")  # Rs 45,000

    with session_factory() as session:
        got = get_guardrails(session, defaults=_safe())

    assert got.max_daily_loss == _ceiling_daily_loss(smaller)
    assert got.max_daily_loss < _ceiling_daily_loss(CAPITAL), "a smaller account must permit a smaller daily loss"
