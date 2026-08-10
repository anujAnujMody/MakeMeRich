"""`te.risk.monitors` — exact boundary/formula mutants not covered by
`test_monitors.py` (which exercises the tiers' qualitative behaviour over
randomised data, never an exact pinned value or an exact threshold edge)."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest

from te.domain.money import Paise
from te.execution.halt import is_halted, is_throttled
from te.persistence.db import make_engine, make_session_factory
from te.persistence.models import Base
from te.persistence.repos.monitors import save_drawdown_envelope
from te.risk import killswitch
from te.risk.monitors import CusumAction, CusumConfig, CusumMonitor, DrawdownEnvelope, SlippageMonitor

NOW = dt.datetime(2026, 7, 29, 10, 0, tzinfo=dt.UTC)


@pytest.fixture(autouse=True)
def _reset_killswitch():
    killswitch.reset_in_process_cache()
    yield
    killswitch.reset_in_process_cache()


@pytest.fixture
def session_factory(tmp_path: Path):
    engine = make_engine(f"sqlite:///{tmp_path / 'monitors_boundaries_test.db'}")
    Base.metadata.create_all(engine)
    return make_session_factory(engine)


# ---------------------------------------------------------------------------
# Tier 0 — SlippageMonitor
# ---------------------------------------------------------------------------


def test_z_score_stays_none_below_min_observations_even_with_real_spread_and_positive_mean(session_factory) -> None:
    """`z` is only computed once `n >= min_observations` AND `stdev > 0` —
    both real, independent guards (`and`, never `or`). Below the observation
    floor with a real (non-zero) spread and a positive mean is exactly the
    shape of input that would trip `breached` if the guard were `or`-ed
    instead: `mean > 0` is already true, so only the observation-count guard
    is standing between this sample and a spurious breach."""
    with session_factory() as session:
        monitor = SlippageMonitor(session, instrument="NIFTY30JUN2626500CE", min_observations=5)
        for i, actual in enumerate((10_020, 10_040, 10_010)):  # n=3 < min_observations=5, real spread, mean > 0
            monitor.observe(Paise(10_000), Paise(actual), ctx=f"fill-{i}")
        session.commit()
        status = monitor.status()

    assert status.n == 3
    assert status.z_score is None
    assert status.breached is False


def test_breached_stays_false_without_crashing_when_z_is_none_and_mean_is_positive(session_factory) -> None:
    """`breached = z is not None and mean > 0 and z >= threshold` — below
    `min_observations`, `z` stays `None`. A real divergence (positive mean)
    with too few observations is exactly the case that would surface an
    `is` vs `is not` bug loudest: flipping to `z is None` short-circuits to
    `True` here (since `z IS None`), then evaluates `mean > 0` (`True`), then
    tries `z >= threshold` on `None` — a `TypeError`, not a quiet wrong
    answer. `status()` must return cleanly with `breached=False`."""
    with session_factory() as session:
        monitor = SlippageMonitor(session, instrument="NIFTY30JUN2626500CE", min_observations=5)
        for i, actual in enumerate((10_020, 10_040)):  # n=2 < min_observations=5, mean > 0
            monitor.observe(Paise(10_000), Paise(actual), ctx=f"fill-{i}")
        session.commit()
        status = monitor.status()

    assert status.z_score is None
    assert status.breached is False


def test_z_score_matches_the_one_sample_z_statistic_formula(session_factory) -> None:
    """`z = mean / (stdev / sqrt(n))` — pinned against an independent
    computation from the same raw diffs, so a `/ -> *` flip on either
    division in the formula produces a visibly different number."""
    import math
    import statistics

    diffs = [10.0, 30.0, 20.0, 40.0, 25.0]  # n=5 == min_observations, real spread, mean > 0
    with session_factory() as session:
        monitor = SlippageMonitor(session, instrument="NIFTY30JUN2626500CE", min_observations=5)
        for i, diff in enumerate(diffs):
            monitor.observe(Paise(10_000), Paise(10_000 + int(diff)), ctx=f"fill-{i}")
        session.commit()
        status = monitor.status()

    mean = statistics.fmean(diffs)
    stdev = statistics.pstdev(diffs)
    expected_z = mean / (stdev / math.sqrt(len(diffs)))
    assert status.z_score == pytest.approx(expected_z)


# ---------------------------------------------------------------------------
# Tier 1 — CusumMonitor
# ---------------------------------------------------------------------------


def test_cusum_s_pos_and_s_neg_match_the_exact_update_formula(session_factory) -> None:
    """One `update()` from a fresh zero state, pinned against the textbook
    CUSUM recurrence by hand: `s_pos = max(0, s_pos + (x - mu0 - k))`,
    `s_neg = max(0, s_neg - (x - mu0 + k))` — an `Add -> Sub` (or `Sub ->
    Add`) flip on either line changes both outputs."""
    cfg = CusumConfig(mu0=0.0, k=0.5, h1=4.0, h2=8.0)
    with session_factory() as session:
        monitor = CusumMonitor(session, key="cusum:formula", config=cfg)
        monitor.update(2.0)
        s_pos, s_neg = monitor.state()

    assert s_pos == pytest.approx(1.5)  # max(0, 0 + (2.0 - 0 - 0.5))
    assert s_neg == pytest.approx(0.0)  # max(0, 0 - (2.0 - 0 + 0.5)) = max(0, -2.5)


def test_cusum_halts_at_exactly_h2(session_factory) -> None:
    cfg = CusumConfig(mu0=0.0, k=0.0, h1=4.0, h2=8.0)
    with session_factory() as session:
        monitor = CusumMonitor(session, key="cusum:h2-edge", config=cfg)
        action = monitor.update(8.0)  # s_pos = max(0, 0 + 8.0) = 8.0 == h2 exactly

    assert action is CusumAction.HALT


def test_cusum_throttles_at_exactly_h1_but_not_yet_h2(session_factory) -> None:
    cfg = CusumConfig(mu0=0.0, k=0.0, h1=4.0, h2=8.0)
    with session_factory() as session:
        monitor = CusumMonitor(session, key="cusum:h1-edge", config=cfg)
        action = monitor.update(4.0)  # s_pos = max(0, 0 + 4.0) = 4.0 == h1 exactly, < h2

    assert action is CusumAction.THROTTLE


def test_cusum_throttle_action_routes_only_through_throttle_never_halt(session_factory) -> None:
    """A THROTTLE-only statistic must trip `killswitch.throttle()` and must
    NOT also trip `killswitch.trip()` (halt) — the two `if`/`elif` branches
    are mutually exclusive by construction, and a `Is -> IsNot` flip on
    either comparison breaks that exclusivity in one direction or the
    other."""
    cfg = CusumConfig(mu0=0.0, k=0.0, h1=4.0, h2=8.0)
    with session_factory() as session:
        monitor = CusumMonitor(session, key="cusum:throttle-only", config=cfg)
        action = monitor.update(5.0)  # s_pos = 5.0: >= h1(4), < h2(8) -> THROTTLE only
        session.commit()

    assert action is CusumAction.THROTTLE
    with session_factory() as session:
        assert is_throttled(session) is True
        assert is_halted(session) is False


# ---------------------------------------------------------------------------
# Tier 2 — DrawdownEnvelope
# ---------------------------------------------------------------------------


def test_drawdown_envelope_does_not_breach_exactly_at_the_stored_percentile(session_factory) -> None:
    """`breached` requires STRICTLY worse than the stored threshold — a
    drawdown exactly AT the stored percentile must not trip the hard halt."""
    with session_factory() as session:
        save_drawdown_envelope(session, run_id="run-edge", percentiles={0.95: -50_000})
        session.commit()

    with session_factory() as session:
        envelope = DrawdownEnvelope.load(session, "run-edge")
        assert envelope.check(-50_000) is False  # exactly at the threshold
        assert envelope.check(-50_001) is True  # one paise worse
