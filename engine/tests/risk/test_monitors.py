"""`te.risk.monitors` — Tier 0 slippage, Tier 1 CUSUM, Tier 2 drawdown
envelope, Tier 3 rolling performance. See the module docstring for the
motivating fact this whole phase is built around: a t-test on daily P&L
takes too long to catch decay, so these earlier/cheaper layers exist."""

from __future__ import annotations

import datetime as dt
import random
import statistics
from pathlib import Path

import pytest

from te.domain.money import Paise
from te.execution.halt import is_halted, is_throttled
from te.persistence.db import make_engine, make_session_factory
from te.persistence.models import Base
from te.persistence.repos.monitors import save_drawdown_envelope
from te.risk import killswitch
from te.risk.monitors import (
    CusumAction,
    CusumConfig,
    CusumMonitor,
    DrawdownEnvelope,
    RollingPerformance,
    SlippageMonitor,
)

NOW = dt.datetime(2026, 7, 29, 10, 0, tzinfo=dt.UTC)


@pytest.fixture(autouse=True)
def _reset_killswitch():
    killswitch.reset_in_process_cache()
    yield
    killswitch.reset_in_process_cache()


@pytest.fixture
def session_factory(tmp_path: Path):
    engine = make_engine(f"sqlite:///{tmp_path / 'monitors_test.db'}")
    Base.metadata.create_all(engine)
    return make_session_factory(engine)


# ---------------------------------------------------------------------------
# Tier 0 — SlippageMonitor
# ---------------------------------------------------------------------------


def test_slippage_monitor_diverges_before_pnl(session_factory) -> None:
    """A modelled cost that is consistently ~20p/lot too optimistic vs
    actual fills. The slippage z-score (computed against the TIGHT expected
    distribution of the divergence itself, stdev ~5p) clears its threshold
    at a handful of observations. A naive P&L-only check would instead have
    to detect this 20p signal buried in ordinary intraday P&L noise, whose
    stdev is orders of magnitude larger (thousands of paise per trade) —
    the number of samples a t-test needs scales with (noise_stdev/signal)^2,
    so at ~250x the noise stdev of the slippage-only comparison, a P&L
    t-test of comparable power would need on the order of 250^2 = 62,500x
    more observations. This test demonstrates the qualitative claim
    directly: Tier 0 breaches well inside one trading session's fill count,
    long before that many P&L observations could ever accumulate."""
    with session_factory() as session:
        monitor = SlippageMonitor(session, instrument="NIFTY30JUN2626500CE")
        rng = random.Random(7)

        breach_at_n: int | None = None
        for i in range(1, 21):
            expected = Paise(10_000)
            actual = Paise(10_000 + 20 + round(rng.gauss(0, 5)))  # +20p systematic, +-5p noise
            monitor.observe(expected, actual, ctx=f"fill-{i}")
            status = monitor.status()
            if status.breached and breach_at_n is None:
                breach_at_n = i
        session.commit()

    assert breach_at_n is not None
    # Breaches well within a single session's typical fill count (dozens at
    # most), nowhere near the tens of thousands of samples a P&L-noise-only
    # t-test of comparable power would require. `<= 20` alone is true by
    # construction (the loop never runs past 20) and would stay green even
    # if `DEFAULT_SLIPPAGE_MIN_OBSERVATIONS` were quadrupled to 20 itself —
    # a concrete bound the loop does not hand us for free closes that gap.
    assert breach_at_n <= 8


def test_deliberately_injected_slippage_fault_trips_tier0_within_one_session(session_factory) -> None:
    """The plan's literal Phase 7 exit criterion: a deliberately injected,
    much-worse-than-modelled slippage pattern trips Tier 0 within ONE
    session's fills, not requiring multiple days of history. Fault
    magnitude: every fill costs a full 150p more than modelled (a large,
    obviously-wrong divergence — e.g. a broken price feed or a
    misconfigured order type), vs a tight +-3p noise band."""
    with session_factory() as session:
        monitor = SlippageMonitor(session, instrument="BANKNIFTY30JUN2650000CE")
        rng = random.Random(99)

        for i in range(1, 13):  # a dozen fills — one session's worth
            expected = Paise(5_000)
            actual = Paise(5_000 + 150 + round(rng.gauss(0, 3)))
            monitor.observe(expected, actual, ctx=f"session-1-fill-{i}", ts=NOW + dt.timedelta(minutes=i))
        session.commit()

    with session_factory() as session:
        assert is_throttled(session) is True  # Tier 0 routes a THROTTLE, not a full halt
        assert is_halted(session) is False


def test_slippage_monitor_does_not_breach_on_favourable_or_noisy_fills(session_factory) -> None:
    with session_factory() as session:
        monitor = SlippageMonitor(session, instrument="NIFTY30JUN2626500CE")
        rng = random.Random(3)
        for i in range(1, 31):
            expected = Paise(10_000)
            actual = Paise(10_000 + round(rng.gauss(0, 5)))  # zero-mean noise, no systematic divergence
            monitor.observe(expected, actual, ctx=f"fill-{i}")
        session.commit()
        status = monitor.status()

    assert status.breached is False
    with session_factory() as session:
        assert is_throttled(session) is False


# ---------------------------------------------------------------------------
# Tier 1 — CusumMonitor
# ---------------------------------------------------------------------------


def test_cusum_throttles_then_halts_on_sustained_negative_shift(session_factory) -> None:
    rng = random.Random(11)
    daily_pnls = [rng.gauss(0, 1) for _ in range(20)]  # 20 stationary days, mean 0, std 1
    daily_pnls += [rng.gauss(-1.5, 1) for _ in range(40)]  # then a sustained -1.5 std shift

    throttle_day: int | None = None
    halt_day: int | None = None
    with session_factory() as session:
        monitor = CusumMonitor(session)
        for day, x in enumerate(daily_pnls):
            action = monitor.update(x)
            if action is CusumAction.THROTTLE and throttle_day is None:
                throttle_day = day
            if action is CusumAction.HALT and halt_day is None:
                halt_day = day
            session.commit()

    assert throttle_day is not None, "THROTTLE never fired on a sustained -1.5 std shift"
    assert halt_day is not None, "HALT never fired on a sustained -1.5 std shift"
    assert throttle_day < halt_day
    # Neither immediate (breaking on day 20, the first post-break day) nor
    # absurdly delayed.
    assert halt_day - 20 >= 1
    assert halt_day - 20 <= 30


def test_cusum_no_false_positive_on_stationary_noise(session_factory) -> None:
    rng = random.Random(42)
    daily_pnls = [rng.gauss(0, 1) for _ in range(500)]

    halt_count = 0
    throttle_count = 0
    with session_factory() as session:
        monitor = CusumMonitor(session)
        for x in daily_pnls:
            action = monitor.update(x)
            if action is CusumAction.HALT:
                halt_count += 1
            elif action is CusumAction.THROTTLE:
                throttle_count += 1
            session.commit()

    assert halt_count == 0
    assert throttle_count / len(daily_pnls) < 0.05  # rare


def test_cusum_state_persists_across_restart(session_factory) -> None:
    with session_factory() as session:
        monitor = CusumMonitor(session, key="cusum:orb")
        monitor.update(-2.0)
        monitor.update(-2.0)
        session.commit()
        s_pos_before, s_neg_before = monitor.state()

    # "restart" — fresh session against the same DB file
    with session_factory() as session:
        restarted = CusumMonitor(session, key="cusum:orb")
        s_pos_after, s_neg_after = restarted.state()

    assert s_pos_after == s_pos_before
    assert s_neg_after == s_neg_before
    assert s_neg_after > 0  # the in-progress breach is still there, not reset to 0


def test_cusum_default_config_has_halt_threshold_above_throttle_threshold() -> None:
    cfg = CusumConfig()
    assert cfg.h2 > cfg.h1 > 0


# ---------------------------------------------------------------------------
# Tier 2 — DrawdownEnvelope
# ---------------------------------------------------------------------------


def test_drawdown_envelope_trips_when_dd_exceeds_stored_95th_percentile(session_factory) -> None:
    with session_factory() as session:
        save_drawdown_envelope(
            session, run_id="run-1", percentiles={0.5: -10_000, 0.95: -50_000, 0.99: -80_000}
        )
        session.commit()

    with session_factory() as session:
        envelope = DrawdownEnvelope.load(session, "run-1")

        assert envelope.check(-40_000) is False  # inside the envelope
        assert envelope.check(-60_000) is True  # worse than the stored 95th percentile


def test_drawdown_envelope_trip_routes_through_killswitch(session_factory) -> None:
    with session_factory() as session:
        save_drawdown_envelope(session, run_id="run-2", percentiles={0.95: -50_000})
        session.commit()

    with session_factory() as session:
        envelope = DrawdownEnvelope.load(session, "run-2")
        breached = envelope.check(-70_000, session=session)
        session.commit()
        assert breached is True

    with session_factory() as session:
        assert is_halted(session) is True


def test_drawdown_envelope_reads_persisted_not_recomputed(session_factory, monkeypatch: pytest.MonkeyPatch) -> None:
    """`check()` must never call `stationary_bootstrap_drawdown_envelope`
    live — proven by making that function raise if called, then confirming
    `check()` still works from persisted data."""
    with session_factory() as session:
        save_drawdown_envelope(session, run_id="run-3", percentiles={0.95: -50_000})
        session.commit()

    import te.backtest.report as report_module

    def _boom(*args: object, **kwargs: object) -> None:
        raise AssertionError(
            "stationary_bootstrap_drawdown_envelope must not be called live by DrawdownEnvelope.check()"
        )

    monkeypatch.setattr(report_module, "stationary_bootstrap_drawdown_envelope", _boom)

    with session_factory() as session:
        envelope = DrawdownEnvelope.load(session, "run-3")
        assert envelope.check(-40_000) is False
        assert envelope.check(-60_000) is True


# ---------------------------------------------------------------------------
# Tier 3 — RollingPerformance
# ---------------------------------------------------------------------------


def test_rolling_performance_never_halts_alone() -> None:
    """Structural check: no method name or return type on `RollingPerformance`
    (or its snapshot) resembles a halt/throttle trigger."""
    import inspect

    from te.risk.monitors import RollingPerformanceSnapshot

    forbidden_substrings = ("halt", "throttle", "trip")
    for name, _member in inspect.getmembers(RollingPerformance):
        if name.startswith("_"):
            continue
        for bad in forbidden_substrings:
            assert bad not in name.lower(), f"RollingPerformance.{name} looks halt-triggering"

    for name in RollingPerformanceSnapshot.__dataclass_fields__:
        for bad in forbidden_substrings:
            assert bad not in name.lower()

    sig = inspect.signature(RollingPerformance.snapshot)
    assert "CusumAction" not in str(sig.return_annotation)
    assert "bool" not in str(sig.return_annotation)


def test_rolling_performance_computes_trailing_sharpe_and_profit_factor(session_factory) -> None:
    from te.persistence.repos.paper_trading import insert_trade

    with session_factory() as session:
        for i, net_pnl in enumerate([1_000, -500, 2_000, -300, 1_500]):
            insert_trade(
                session,
                client_order_id=f"c-{i}",
                symbol="NIFTY30JUN2626500CE",
                exchange="NFO",
                strategy="orb",
                direction="long_call",
                lots=1,
                lot_size=65,
                entry_premium=Paise(3_000),
                exit_premium=Paise(3_000),
                gross_pnl=Paise(net_pnl),
                costs=Paise(0),
                net_pnl=Paise(net_pnl),
                exit_reason="target" if net_pnl > 0 else "stop",
                opened_at=NOW - dt.timedelta(hours=1),
                closed_at=NOW + dt.timedelta(days=i),
            )
        session.commit()

        rp = RollingPerformance(session, window_sessions=20)
        snap = rp.snapshot()

    assert snap.n_trades == 5
    assert snap.n_sessions == 5
    assert snap.trailing_sharpe is not None
    assert snap.trailing_profit_factor is not None
    expected_pf = (1_000 + 2_000 + 1_500) / (500 + 300)
    assert round(snap.trailing_profit_factor, 6) == round(expected_pf, 6)
    expected_sharpe = statistics.mean([1_000, -500, 2_000, -300, 1_500]) / statistics.stdev(
        [1_000, -500, 2_000, -300, 1_500]
    )
    assert round(snap.trailing_sharpe, 6) == round(expected_sharpe, 6)


def test_rolling_performance_truncates_to_the_configured_window(session_factory) -> None:
    """25 sessions against a 20-session window: only the 20 MOST RECENT
    sessions may feed the statistic — the fixture in
    `test_rolling_performance_computes_trailing_sharpe_and_profit_factor`
    seeds only 5 sessions against a 20-session window, so the window itself
    is never exercised there. `recent_session_dates(limit=window_sessions)`
    is the only thing enforcing the cutoff; widening that limit (e.g. to
    `window_sessions * 100`) would silently turn "trailing 20 sessions" into
    "lifetime" while every other test in this file stays green."""
    from te.persistence.repos.paper_trading import insert_trade

    net_pnls = [100 * (i + 1) if i % 2 == 0 else -50 * (i + 1) for i in range(25)]
    with session_factory() as session:
        for i, net_pnl in enumerate(net_pnls):
            insert_trade(
                session,
                client_order_id=f"c-{i}",
                symbol="NIFTY30JUN2626500CE",
                exchange="NFO",
                strategy="orb",
                direction="long_call",
                lots=1,
                lot_size=65,
                entry_premium=Paise(3_000),
                exit_premium=Paise(3_000),
                gross_pnl=Paise(net_pnl),
                costs=Paise(0),
                net_pnl=Paise(net_pnl),
                exit_reason="target" if net_pnl > 0 else "stop",
                opened_at=NOW - dt.timedelta(hours=1),
                closed_at=NOW + dt.timedelta(days=i),
            )
        session.commit()

        rp = RollingPerformance(session, window_sessions=20)
        snap = rp.snapshot()

    included = net_pnls[-20:]  # the 20 most recent sessions/trades, oldest 5 excluded
    assert snap.n_sessions == 20
    assert snap.n_trades == 20
    expected_pf = sum(x for x in included if x > 0) / abs(sum(x for x in included if x < 0))
    assert round(snap.trailing_profit_factor, 6) == round(expected_pf, 6)
    expected_sharpe = statistics.mean(included) / statistics.stdev(included)
    assert round(snap.trailing_sharpe, 6) == round(expected_sharpe, 6)


def test_rolling_performance_empty_when_no_trades(session_factory) -> None:
    with session_factory() as session:
        rp = RollingPerformance(session)
        snap = rp.snapshot()

    assert snap.n_sessions == 0
    assert snap.n_trades == 0
    assert snap.trailing_sharpe is None
    assert snap.trailing_profit_factor is None
