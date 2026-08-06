"""What the Strategies page is allowed to say.

The page lists 32 strategies side by side, which is precisely the shape that
manufactures false winners: the best of N noise draws looks excellent by
construction. Everything here pins the rules that stop the page becoming a
faster way to fool the reader.

The load-bearing one is that `confidence` carries the LUCK-ADJUSTED score.
A raw backtest number in that field would look identical on screen and be
completely different in meaning — the library's best raw win rate is 52.1%
while its best luck-adjusted score is 0.035 against a bar of 0.95.
"""

from __future__ import annotations

import datetime as dt

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import sessionmaker

from te.api.strategy_cards import strategy_card
from te.backtest.results_store import load_results, save_results
from te.backtest.strategy_lab import BacktestResult
from te.engine.strategy_config import DEFAULT_ENABLED, enabled_strategies, set_strategy_enabled
from te.persistence.models import Base
from te.strategy.registry import all_specs, available, spec


@pytest.fixture
def session(tmp_path):  # noqa: ANN001, ANN201
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as s:
        yield s


def _result(name: str, *, trades: int = 1200, win_rate: float = 0.47, deflated: float = 0.0) -> BacktestResult:
    return BacktestResult(
        strategy=name,
        instrument="NIFTY",
        trades=trades,
        unlabelled=0,
        win_rate=win_rate,
        mean_r=-0.062,
        sharpe=-0.06,
        t_stat=-2.27,
        deflated=deflated,
        n_trials_at_scoring=83,
        first_day=dt.date(2024, 1, 1),
        last_day=dt.date(2026, 7, 31),
    )


def _saved(session, results: list[BacktestResult]):  # noqa: ANN001, ANN202
    save_results(
        session,
        results,
        run_id="test",
        stop_pct=20.0,
        target_pct=20.0,
        max_hold_minutes=180,
        strikes_out_of_the_money=0,
    )
    session.commit()
    return load_results(session)


def test_confidence_is_the_luck_adjusted_score_not_the_win_rate(session) -> None:  # noqa: ANN001
    """The whole point. A strategy winning 47% of the time with a deflated
    score of 0.03 must show 3.0 confidence, never 47."""
    stored = _saved(session, [_result("orb60", win_rate=0.47, deflated=0.03)])
    card = strategy_card(spec("orb60"), enabled=True, result=stored["orb60"])
    assert card.confidence == pytest.approx(3.0), "confidence must be the deflated score, not the win rate"
    assert card.winRate == pytest.approx(47.0)


def test_a_losing_strategy_never_shows_an_upward_trend(session) -> None:  # noqa: ANN001
    """The trend arrow is the smallest possible lie with the largest
    consequence — it is the one thing a user reads at a glance."""
    stored = _saved(session, [_result("orb60", deflated=0.03)])
    card = strategy_card(spec("orb60"), enabled=True, result=stored["orb60"])
    assert card.confidenceTrend == "stable"


def test_only_a_strategy_that_clears_the_bar_trends_up(session) -> None:  # noqa: ANN001
    stored = _saved(session, [_result("orb60", deflated=0.97)])
    card = strategy_card(spec("orb60"), enabled=True, result=stored["orb60"])
    assert card.confidenceTrend == "up"


def test_an_untested_strategy_shows_zeros_not_placeholders() -> None:
    """Standing project rule: no win rate and no confidence without a real
    sample behind it. A plausible-looking placeholder is worse than a zero,
    because a zero is obviously not a measurement."""
    card = strategy_card(spec("supertrend"), enabled=False, result=None)
    assert card.winRate == 0.0
    assert card.confidence == 0.0
    assert card.totalTrades == 0
    assert card.confidenceTrend == "stable"


def test_backtest_results_are_never_shown_as_realised_profit(session) -> None:  # noqa: ANN001
    """None of these has traded real money. `weeklyPnl` renders as rupees on
    the card, so a backtest figure there would read as money earned."""
    stored = _saved(session, [_result("orb60", deflated=0.9)])
    card = strategy_card(spec("orb60"), enabled=True, result=stored["orb60"])
    assert card.weeklyPnl == 0.0


def test_every_registered_strategy_can_be_rendered(session) -> None:  # noqa: ANN001
    """A card that raises takes the whole page down, so the builder must
    tolerate every spec in the library, tested or not."""
    for spec_obj in all_specs():
        card = strategy_card(spec_obj, enabled=False, result=None)
        assert card.id == spec_obj.name
        assert card.rule.strip()


def test_the_default_is_only_what_the_engine_already_runs(session) -> None:  # noqa: ANN001
    """Registering 32 strategies must not switch 32 strategies on. Nothing
    in the library has beaten a coin flip, so an 'all on' default would put
    the engine into 32 simultaneous losing strategies."""
    assert enabled_strategies(session) == DEFAULT_ENABLED
    assert enabled_strategies(session) == frozenset({"orb"})


def test_the_coin_flip_control_can_never_be_switched_on(session) -> None:  # noqa: ANN001
    """A control that could be armed from the UI is not a control."""
    with pytest.raises(ValueError, match="measure-only"):
        set_strategy_enabled(session, "random_entry", enabled=True)
    assert "random_entry" not in enabled_strategies(session)


def test_the_control_can_still_be_switched_off_without_error(session) -> None:  # noqa: ANN001
    """Disabling something already disabled must be a no-op, not a crash —
    the UI sends whatever state it thinks it is toggling to."""
    set_strategy_enabled(session, "random_entry", enabled=False)
    assert "random_entry" not in enabled_strategies(session)


def test_enabling_survives_a_reload(session) -> None:  # noqa: ANN001
    """The toggle must reach the engine, which reads this fresh every cycle —
    the whole reason it lives in `engine_state` and not in memory."""
    set_strategy_enabled(session, "supertrend", enabled=True)
    session.commit()
    assert "supertrend" in enabled_strategies(session)
    set_strategy_enabled(session, "supertrend", enabled=False)
    session.commit()
    assert "supertrend" not in enabled_strategies(session)


def test_an_unknown_strategy_is_refused(session) -> None:  # noqa: ANN001
    with pytest.raises(ValueError, match="unknown strategy"):
        set_strategy_enabled(session, "not_a_strategy", enabled=True)


def test_results_round_trip_with_the_settings_they_were_measured_at(session) -> None:  # noqa: ANN001
    """A score is only comparable against another measured the same way, so
    the geometry travels with the result rather than being assumed."""
    stored = _saved(session, [_result("orb60")])["orb60"]
    assert stored.stop_pct == 20.0
    assert stored.target_pct == 20.0
    assert stored.max_hold_minutes == 180
    assert stored.strikes_out_of_the_money == 0
    assert stored.n_trials_at_scoring == 83, "the trial count must survive — a deflated score without it is unreadable"


def test_missing_results_do_not_break_the_page(session) -> None:  # noqa: ANN001
    """A database that has never had a backtest run must render an honest
    empty page, not a 500."""
    assert load_results(session) == {}
    cards = [strategy_card(s, enabled=s.name in DEFAULT_ENABLED, result=None) for s in all_specs()]
    assert len(cards) == len(available())
    assert all(c.totalTrades == 0 for c in cards)
