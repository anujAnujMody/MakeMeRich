from __future__ import annotations

import datetime as dt
from decimal import Decimal
from pathlib import Path

import pytest

from te.domain.money import Paise
from te.engine.state import (
    AccountGuardrails,
    InstrumentSelection,
    get_guardrails,
    get_instrument_selections,
    get_mode,
    get_run_state,
    instrument_selections_defaults_from_settings,
    set_guardrails,
    set_instrument_selections,
    set_mode,
    set_run_state,
)
from te.persistence.db import make_engine, make_session_factory
from te.persistence.models import Base, Instrument
from te.settings import Settings

# Every value here sits at or below the hard ceilings in `te.engine.state`
# (added 2026-08-01). It previously carried 40% position size, 2% risk per
# trade and a daily loss limit of half the capital — all legal under the old
# `(0, 100]` validation, none of them survivable as a policy.
_DEFAULTS = AccountGuardrails(
    capital=Paise(2_000_000),
    max_daily_loss=Paise(100_000),  # 5% of capital
    max_position_size_pct=Decimal(25),
    max_drawdown_pct=Decimal(15),
    max_trades_per_day=20,
    max_concurrent_positions=5,
    risk_per_trade_pct=Decimal(1),
)


@pytest.fixture
def session_factory(tmp_path: Path):  # noqa: ANN201
    engine = make_engine(f"sqlite:///{tmp_path / 'state_test.db'}")
    Base.metadata.create_all(engine)
    return make_session_factory(engine)


def test_mode_defaults_to_dry_run(session_factory) -> None:  # noqa: ANN001
    with session_factory() as session:
        assert get_mode(session) == "dry-run"


def test_run_state_defaults_to_paused(session_factory) -> None:  # noqa: ANN001
    with session_factory() as session:
        assert get_run_state(session) == "paused"


def test_mode_persists_across_restart(session_factory, tmp_path: Path) -> None:  # noqa: ANN001
    with session_factory() as session:
        set_mode(session, "live")
        session.commit()

    with session_factory() as session:
        assert get_mode(session) == "live"

    engine2 = make_engine(f"sqlite:///{tmp_path / 'state_test.db'}")
    from te.persistence.db import make_session_factory as _msf

    restarted = _msf(engine2)
    with restarted() as session:
        assert get_mode(session) == "live"


def test_set_mode_rejects_unknown_value(session_factory) -> None:  # noqa: ANN001
    with session_factory() as session, pytest.raises(ValueError, match="unknown mode"):
        set_mode(session, "banana")  # type: ignore[arg-type]


def test_run_state_round_trips(session_factory) -> None:  # noqa: ANN001
    with session_factory() as session:
        set_run_state(session, "running")
        session.commit()
    with session_factory() as session:
        assert get_run_state(session) == "running"


def test_guardrails_fall_back_to_defaults_when_no_row_exists(session_factory) -> None:  # noqa: ANN001
    """The exact property that makes this safe to ship tonight: an operator
    who never touches Settings gets today's env-var-derived behaviour,
    byte-for-byte, not a crash or a silently-zeroed config."""
    with session_factory() as session:
        assert get_guardrails(session, defaults=_DEFAULTS) == _DEFAULTS


def test_guardrails_round_trip(session_factory) -> None:  # noqa: ANN001
    changed = AccountGuardrails(
        capital=Paise(3_000_000),
        max_daily_loss=Paise(150_000),  # 5% of capital
        max_position_size_pct=Decimal("22.5"),
        max_drawdown_pct=Decimal(20),
        max_trades_per_day=10,
        max_concurrent_positions=2,
        risk_per_trade_pct=Decimal("1.5"),
    )
    with session_factory() as session:
        set_guardrails(session, changed)
        session.commit()
    with session_factory() as session:
        assert get_guardrails(session, defaults=_DEFAULTS) == changed


def test_guardrails_provenance_is_seed_for_every_field_on_a_fresh_db(session_factory) -> None:  # noqa: ANN001
    from te.engine.state import get_guardrails_provenance

    with session_factory() as session:
        provenance = get_guardrails_provenance(session)

    assert provenance == {
        "capital": "seed",
        "max_daily_loss": "seed",
        "max_position_size_pct": "seed",
        "max_drawdown_pct": "seed",
        "max_trades_per_day": "seed",
        "max_concurrent_positions": "seed",
        "risk_per_trade_pct": "seed",
    }


def test_guardrails_provenance_is_stored_once_saved(session_factory) -> None:  # noqa: ANN001
    """The exact case the API must surface: a dashboard save makes the
    stored value authoritative, and any settings/env default for the same
    field is from then on ignored for trading."""
    from te.engine.state import get_guardrails_provenance

    with session_factory() as session:
        set_guardrails(session, _DEFAULTS)
        session.commit()

    with session_factory() as session:
        provenance = get_guardrails_provenance(session)

    assert provenance == {
        "capital": "stored",
        "max_daily_loss": "stored",
        "max_position_size_pct": "stored",
        "max_drawdown_pct": "stored",
        "max_trades_per_day": "stored",
        "max_concurrent_positions": "stored",
        "risk_per_trade_pct": "stored",
    }


def test_lowering_capital_clears_the_peak_equity_watermark(session_factory) -> None:  # noqa: ANN001
    """Regression, found by review: `te.risk.limits.check_max_drawdown`'s
    peak-equity watermark tracks capital + P&L. Editing capital down with
    no rebase would make the edit itself look like a real trading loss and
    could halt the engine with zero money actually lost.

    CLEARED, not rewritten. Two earlier rules were both wrong: shifting the
    peak by the capital delta assumed equity still carried lifetime P&L, and
    writing the new capital assumed the book was flat. Only the trading loop
    can compute equity — it needs unrealized P&L, which needs a `BarStore`
    this module sits below — so `check_max_drawdown` seeds the watermark
    from real equity on the next cycle instead. See
    `te.engine.state.clear_peak_equity_paise`."""
    import dataclasses

    from te.engine.state import get_peak_equity_paise, set_peak_equity_paise

    with session_factory() as session:
        set_guardrails(session, _DEFAULTS)  # capital = 2,000,000p
        set_peak_equity_paise(session, Paise(2_000_000))  # peak established at the original capital
        session.commit()

    # The daily loss limit moves with capital in the SAME payload: it is
    # defined as a percentage of capital, so cutting capital without cutting
    # it would leave a limit above the 5% ceiling — `set_guardrails`
    # validates the payload as a whole and rejects that, deliberately.
    lowered = dataclasses.replace(
        _DEFAULTS, capital=Paise(1_400_000), max_daily_loss=Paise(70_000)
    )  # capital cut by 600,000p
    with session_factory() as session:
        set_guardrails(session, lowered)
        session.commit()

    with session_factory() as session:
        # Gone, not frozen at the old (now unreachable) higher level and
        # not rewritten from a layer that cannot see open positions.
        assert get_peak_equity_paise(session) is None


def test_raising_capital_also_clears_the_peak_equity_watermark(session_factory) -> None:  # noqa: ANN001
    """Same rule in the other direction — and the direction that matters
    more, because a stale HIGH peak makes the account look permanently
    underwater after a top-up."""
    import dataclasses

    from te.engine.state import get_peak_equity_paise, set_peak_equity_paise

    with session_factory() as session:
        set_guardrails(session, _DEFAULTS)  # capital = 2,000,000p
        set_peak_equity_paise(session, Paise(2_000_000))
        session.commit()

    raised = dataclasses.replace(_DEFAULTS, capital=Paise(2_500_000))
    with session_factory() as session:
        set_guardrails(session, raised)
        session.commit()

    with session_factory() as session:
        assert get_peak_equity_paise(session) is None


def test_setting_guardrails_with_no_prior_peak_leaves_the_watermark_unset(session_factory) -> None:  # noqa: ANN001
    """No peak has ever been recorded yet (e.g. brand-new deployment) —
    a capital edit must not fabricate one."""
    from te.engine.state import get_peak_equity_paise

    with session_factory() as session:
        set_guardrails(session, _DEFAULTS)
        session.commit()

    with session_factory() as session:
        assert get_peak_equity_paise(session) is None


def test_guardrails_persist_across_restart(session_factory, tmp_path: Path) -> None:  # noqa: ANN001
    with session_factory() as session:
        set_guardrails(
            session,
            AccountGuardrails(
                capital=Paise(3_000_000),
                max_daily_loss=Paise(150_000),  # 5% of capital
                max_position_size_pct=Decimal(25),
                max_drawdown_pct=Decimal(20),
                max_trades_per_day=10,
                max_concurrent_positions=2,
                risk_per_trade_pct=Decimal(1),
            ),
        )
        session.commit()

    restarted = make_session_factory(make_engine(f"sqlite:///{tmp_path / 'state_test.db'}"))
    with restarted() as session:
        assert get_guardrails(session, defaults=_DEFAULTS).capital == Paise(3_000_000)


@pytest.mark.parametrize(
    "field,bad_value",
    [
        ("capital", Paise(0)),
        ("capital", Paise(-1)),
        ("max_daily_loss", Paise(-1)),
        ("max_position_size_pct", Decimal(0)),
        ("max_position_size_pct", Decimal(101)),
        ("max_drawdown_pct", Decimal(0)),
        ("max_trades_per_day", 0),
        ("max_concurrent_positions", 0),
        ("risk_per_trade_pct", Decimal(0)),
    ],
)
def test_set_guardrails_rejects_invalid_values(session_factory, field: str, bad_value: object) -> None:  # noqa: ANN001
    import dataclasses

    invalid = dataclasses.replace(_DEFAULTS, **{field: bad_value})
    with session_factory() as session, pytest.raises(ValueError):
        set_guardrails(session, invalid)


def test_set_guardrails_rejects_invalid_all_or_nothing(session_factory) -> None:  # noqa: ANN001
    """One invalid field must not partially write the others — verified by
    confirming a rejected save leaves the DB exactly as the valid defaults
    fixture (`test_guardrails_fall_back_to_defaults_when_no_row_exists`)
    would read, not a mix of old and new values."""
    import dataclasses

    with session_factory() as session:
        set_guardrails(session, _DEFAULTS)
        session.commit()

    invalid = dataclasses.replace(_DEFAULTS, capital=Paise(9_000_000), max_trades_per_day=0)
    with session_factory() as session, pytest.raises(ValueError):
        set_guardrails(session, invalid)

    with session_factory() as session:
        assert get_guardrails(session, defaults=_DEFAULTS) == _DEFAULTS


def _settings(**overrides: object) -> Settings:
    defaults: dict[str, object] = {
        "env": "dev",
        "database_url": "sqlite:///:memory:",
        "bar_store_path": Path("data/bars"),
        "openalgo_host": "http://openalgo:5000",
        "openalgo_ws_host": "ws://openalgo:8765",
        "openalgo_api_key": "test-key",
        "cors_origins": ["http://localhost:5173"],
        "paper_cycle_instruments": ["NIFTY"],
        "paper_cycle_exchange": "NFO",
        "paper_cycle_lot_size": 65,
    }
    defaults.update(overrides)
    return Settings(**defaults)  # type: ignore[arg-type]


def test_instrument_selection_defaults_seed_all_4_underlyings_with_correct_exchange() -> None:
    """Regression: before this, only symbols in `paper_cycle_instruments`
    (NIFTY by default) had ANY seeded default — a user enabling SENSEX/
    BANKEX from the dashboard had nothing pre-populated with the correct
    exchange to toggle on. Now all 4 known underlyings are always present,
    each with the one real exchange baked in, active only for whatever's
    configured today."""
    defaults = instrument_selections_defaults_from_settings(_settings(paper_cycle_instruments=["NIFTY"]))

    by_symbol = {s.symbol: s for s in defaults}
    assert set(by_symbol) == {"NIFTY", "BANKNIFTY", "SENSEX", "BANKEX"}
    assert by_symbol["NIFTY"].exchange == "NFO"
    assert by_symbol["BANKNIFTY"].exchange == "NFO"
    assert by_symbol["SENSEX"].exchange == "BFO"
    assert by_symbol["BANKEX"].exchange == "BFO"
    assert by_symbol["NIFTY"].active is True
    assert by_symbol["BANKNIFTY"].active is False
    assert by_symbol["SENSEX"].active is False
    assert by_symbol["BANKEX"].active is False


def test_get_instrument_selections_overrides_lot_size_from_the_real_synced_table(session_factory) -> None:  # noqa: ANN001
    """The single most important behaviour of this pair: whatever lot_size
    is SAVED (here, a deliberately wrong 999) is never what actually gets
    traded once the real broker-synced value exists — `get_instrument_
    selections` always resolves it fresh."""
    with session_factory() as session:
        set_instrument_selections(
            session,
            (InstrumentSelection(symbol="NIFTY", exchange="NFO", lot_size=999, active=True),),
        )
        session.add(
            Instrument(
                symbol="NIFTY25AUG26FUT",
                exchange="NFO",
                name="NIFTY",
                instrument_type="FUT",
                expiry="25-AUG-26",
                strike=0.0,
                lot_size=65,
                tick_size=0.05,
                source="openalgo",
                updated_at=dt.datetime.now(dt.UTC),
            )
        )
        session.commit()

    with session_factory() as session:
        selections = get_instrument_selections(
            session, defaults=instrument_selections_defaults_from_settings(_settings())
        )

    assert selections[0].symbol == "NIFTY"
    assert selections[0].lot_size == 65  # NOT the stale/wrong 999 that was saved


def test_get_instrument_selections_falls_back_to_stored_lot_size_when_nothing_synced_yet(session_factory) -> None:  # noqa: ANN001
    with session_factory() as session:
        set_instrument_selections(
            session,
            (InstrumentSelection(symbol="NIFTY", exchange="NFO", lot_size=65, active=True),),
        )
        session.commit()

    with session_factory() as session:
        selections = get_instrument_selections(
            session, defaults=instrument_selections_defaults_from_settings(_settings())
        )

    assert selections[0].lot_size == 65


def test_get_instrument_selections_forces_inactive_when_an_active_instrument_has_never_synced(session_factory) -> None:  # noqa: ANN001
    """Regression, found by review: an active instrument whose underlying
    has never been confirmed by a real broker sync used to silently trade
    on the stored/default lot size — e.g. NIFTY's 65 applied to SENSEX
    (real lot size 20) — corrupting sizing indefinitely with no signal
    anywhere. It must never trade until a real sync exists."""
    with session_factory() as session:
        set_instrument_selections(
            session,
            (InstrumentSelection(symbol="SENSEX", exchange="BFO", lot_size=65, active=True),),
        )
        session.commit()

    with session_factory() as session:
        selections = get_instrument_selections(
            session, defaults=instrument_selections_defaults_from_settings(_settings())
        )

    assert selections[0].active is False


def test_get_instrument_selections_leaves_an_already_inactive_instrument_alone_when_unsynced(session_factory) -> None:  # noqa: ANN001
    with session_factory() as session:
        set_instrument_selections(
            session,
            (InstrumentSelection(symbol="BANKEX", exchange="BFO", lot_size=30, active=False),),
        )
        session.commit()

    with session_factory() as session:
        selections = get_instrument_selections(
            session, defaults=instrument_selections_defaults_from_settings(_settings())
        )

    assert selections[0].active is False
    assert selections[0].lot_size == 30  # untouched — no real sync to override it with either
