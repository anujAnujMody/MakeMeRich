"""Live decay monitors — four tiers, fastest/most-sensitive to slowest/most-
authoritative, per the plan's Phase 7. **Motivating fact**: you cannot
detect Sharpe/edge decay with a simple t-test in useful time — by the time a
mean-P&L shift clears conventional statistical significance, real money is
already lost. So detection happens at three earlier, cheaper layers before
P&L-based statistics would ever fire, plus one purely informational one:

- **Tier 0** (`SlippageMonitor`) — live fills vs `te.domain.costs.CostModel`'s
  modelled expectation. Diverges BEFORE P&L does: a systematic fill-price
  divergence of a few paise per lot is invisible in daily P&L noise for many
  sessions, but produces a large z-score against its OWN (much tighter)
  expected distribution after a handful of observations.
- **Tier 1** (`CusumMonitor`) — CUSUM control chart on daily P&L,
  standardized to volatility units first (so the statistic is regime-
  independent) — throttles, then halts, on a sustained shift.
- **Tier 2** (`DrawdownEnvelope`) — current drawdown vs the STORED (never
  recomputed live) 95th-percentile of the backtest's own bootstrapped
  max-drawdown distribution (`te.backtest.report.stationary_bootstrap_drawdown_envelope`).
  Hard halt.
- **Tier 3** (`RollingPerformance`) — trailing Sharpe/profit-factor. Human-
  review / dashboard signal ONLY — the plan is explicit this tier alone is
  insufficient as a trigger, so this class exposes no halt-shaped method.

All halting/throttling tiers (0, 1, 2) route through `te.risk.killswitch`'s
existing DB-flag mechanism (`trip()`/`throttle()`) — no parallel halt
mechanism is invented here.
"""

from __future__ import annotations

import datetime as dt
import math
import statistics
from dataclasses import dataclass
from enum import StrEnum

from sqlalchemy.orm import Session

from te.backtest.report import profit_factor, sharpe
from te.domain.money import Paise
from te.persistence.repos.monitors import (
    get_monitor_state,
    insert_slippage_observation,
    load_drawdown_envelope,
    recent_slippage_observations,
    upsert_monitor_state,
)
from te.persistence.repos.paper_trading import recent_session_dates, trades_closed_since
from te.risk import killswitch

# ---------------------------------------------------------------------------
# Tier 0 — slippage
# ---------------------------------------------------------------------------

#: One-sided z-score threshold for flagging a systematic (not noise) fill
#: divergence. 2.5 ~ p<0.006 one-sided under a normal approximation —
#: conservative enough that pure noise rarely trips it, but far looser than
#: the n≈dozens-to-hundreds of sessions a daily-P&L t-test would need to
#: reach the same confidence on a divergence of comparable economic size
#: (see `tests/risk/test_monitors.py::test_slippage_monitor_diverges_before_pnl`
#: for a worked comparison).
DEFAULT_SLIPPAGE_Z_THRESHOLD = 2.5

#: Refuse to compute a z-score (leave it `None`, `breached=False`) below this
#: many observations — protects against a false trip on 1-2 noisy fills
#: before the sample mean/stdev mean anything.
DEFAULT_SLIPPAGE_MIN_OBSERVATIONS = 5


@dataclass(frozen=True)
class SlippageStatus:
    """`n`/`mean_diff_paise`/`stdev_diff_paise` describe the rolling window
    of `actual_paise - expected_paise` observations. `z_score` is the
    one-sample z-statistic of that mean against 0 (CostModel's modelled
    expectation of no systematic divergence); `None` below
    `min_observations`. `breached` is true only for a POSITIVE (costlier
    than modelled) divergence whose z-score clears the configured
    threshold — a favourable divergence never trips anything."""

    n: int
    mean_diff_paise: float
    stdev_diff_paise: float
    z_score: float | None
    breached: bool


class SlippageMonitor:
    """Tier 0. `observe()` persists one (expected, actual) fill-cost pair to
    `slippage_observations` (survives a restart) and, if the resulting
    status is breached, routes a THROTTLE through `te.risk.killswitch` —
    the earliest-possible warning, so the response is "reduce size", not
    yet "halt" (Tier 1/2 own the halt escalation)."""

    def __init__(
        self,
        session: Session,
        *,
        instrument: str,
        window_size: int = 200,
        z_threshold: float = DEFAULT_SLIPPAGE_Z_THRESHOLD,
        min_observations: int = DEFAULT_SLIPPAGE_MIN_OBSERVATIONS,
    ) -> None:
        self._session = session
        self._instrument = instrument
        self._window_size = window_size
        self._z_threshold = z_threshold
        self._min_observations = min_observations

    def observe(self, expected_paise: Paise, actual_paise: Paise, ctx: str, *, ts: dt.datetime | None = None) -> None:
        insert_slippage_observation(
            self._session,
            ts=ts or dt.datetime.now(dt.UTC),
            instrument=self._instrument,
            expected_paise=int(expected_paise),
            actual_paise=int(actual_paise),
            context=ctx,
        )
        status = self.status()
        if status.breached:
            killswitch.throttle(
                self._session,
                f"Tier 0 slippage monitor breach on {self._instrument!r}: "
                f"mean divergence {status.mean_diff_paise:.2f}p over {status.n} observations, "
                f"z={status.z_score:.2f} (threshold {self._z_threshold})",
            )

    def status(self) -> SlippageStatus:
        rows = recent_slippage_observations(self._session, instrument=self._instrument, limit=self._window_size)
        diffs = [float(row.diff_paise) for row in rows]
        n = len(diffs)
        if n == 0:
            return SlippageStatus(n=0, mean_diff_paise=0.0, stdev_diff_paise=0.0, z_score=None, breached=False)

        mean = statistics.fmean(diffs)
        stdev = statistics.pstdev(diffs) if n > 1 else 0.0

        z: float | None = None
        if n >= self._min_observations and stdev > 0:
            z = mean / (stdev / math.sqrt(n))

        breached = z is not None and mean > 0 and z >= self._z_threshold
        return SlippageStatus(n=n, mean_diff_paise=mean, stdev_diff_paise=stdev, z_score=z, breached=breached)


# ---------------------------------------------------------------------------
# Tier 1 — CUSUM
# ---------------------------------------------------------------------------


class CusumAction(StrEnum):
    NONE = "none"
    THROTTLE = "throttle"
    HALT = "halt"


@dataclass(frozen=True)
class CusumConfig:
    """`mu0` — expected daily net P&L in standardized (vol-unit) terms, from
    the backtest (0.0, or a small positive drift, are both reasonable
    defaults; this deployment's default is 0.0 — "no systematic daily edge
    assumed" is the more conservative starting point absent a specific
    backtest figure). `k` — the CUSUM slack, in std-dev units; **default
    0.5**, i.e. tuned to detect roughly a 1-std-dev sustained shift (the
    textbook `k = delta/2` choice for a shift of size `delta`). `h1`
    (throttle) / `h2` (halt), `h2 > h1` — with `k=0.5`, **`h1=4`/`h2=8`**
    give an in-control average run length (time between false alarms on
    pure noise) that stayed clean over 100 independent 500-session pure-
    noise Monte Carlo runs in >90% of seeds, and the rare false HALTs that
    did occur were always late (none before session ~130) — while still
    detecting a sustained 1.5-std shift within single-digit-to-low-teens
    days of it starting; see
    `tests/risk/test_monitors.py::test_cusum_throttles_then_halts_on_sustained_negative_shift`
    and `::test_cusum_no_false_positive_on_stationary_noise` for the exact
    calibration this was checked against."""

    mu0: float = 0.0
    k: float = 0.5
    h1: float = 4.0
    h2: float = 8.0


DEFAULT_CUSUM_CONFIG = CusumConfig()


class CusumMonitor:
    """Tier 1. Standard two-sided CUSUM on `daily_pnl_vol_units` (the
    caller standardizes today's net P&L by its OWN historical stdev before
    calling `update()`, so this class never needs to know the currency
    scale or the regime). State (`S+`, `S-`) is persisted to `monitor_state`
    under `key` after every `update()`, so a restart mid-breach doesn't
    silently reset the count."""

    def __init__(
        self, session: Session, *, key: str = "cusum:default", config: CusumConfig = DEFAULT_CUSUM_CONFIG
    ) -> None:
        self._session = session
        self._key = key
        self._config = config
        if get_monitor_state(session, key) is None:
            upsert_monitor_state(session, key, s_pos=0.0, s_neg=0.0, last_action=CusumAction.NONE.value)

    def state(self) -> tuple[float, float]:
        """Current `(S+, S-)`."""
        row = get_monitor_state(self._session, self._key)
        assert row is not None
        return row.s_pos, row.s_neg

    def update(self, daily_pnl_vol_units: float) -> CusumAction:
        row = get_monitor_state(self._session, self._key)
        assert row is not None
        cfg = self._config

        s_pos = max(0.0, row.s_pos + (daily_pnl_vol_units - cfg.mu0 - cfg.k))
        s_neg = max(0.0, row.s_neg - (daily_pnl_vol_units - cfg.mu0 + cfg.k))
        statistic = max(s_pos, s_neg)

        if statistic >= cfg.h2:
            action = CusumAction.HALT
        elif statistic >= cfg.h1:
            action = CusumAction.THROTTLE
        else:
            action = CusumAction.NONE

        upsert_monitor_state(self._session, self._key, s_pos=s_pos, s_neg=s_neg, last_action=action.value)

        if action is CusumAction.HALT:
            killswitch.trip(
                self._session,
                f"Tier 1 CUSUM halt ({self._key}): statistic={statistic:.3f} >= h2={cfg.h2}",
            )
        elif action is CusumAction.THROTTLE:
            killswitch.throttle(
                self._session,
                f"Tier 1 CUSUM throttle ({self._key}): statistic={statistic:.3f} >= h1={cfg.h1}",
            )

        return action


# ---------------------------------------------------------------------------
# Tier 2 — drawdown envelope
# ---------------------------------------------------------------------------


class DrawdownEnvelope:
    """Tier 2. Wraps a PERSISTED `te.backtest.report.stationary_bootstrap_drawdown_envelope`
    result (percentile -> paise, always `<= 0`) — never recomputes the
    bootstrap live. `check()` compares a current drawdown figure (also in
    paise, matching the codebase-wide ban on float-rupee money values —
    this deviates from the plan's literal `current_dd_pct` naming only in
    unit, not in behaviour) against the stored percentile."""

    def __init__(self, percentiles: dict[float, int]) -> None:
        self._percentiles = percentiles

    @classmethod
    def load(cls, session: Session, run_id: str) -> DrawdownEnvelope:
        percentiles = load_drawdown_envelope(session, run_id)
        if not percentiles:
            raise ValueError(f"no persisted drawdown envelope for run_id={run_id!r} — save one first")
        return cls(percentiles)

    def check(self, current_drawdown_paise: int, *, percentile: float = 0.95, session: Session | None = None) -> bool:
        """`True` = hard halt: `current_drawdown_paise` is WORSE (more
        negative) than the stored `percentile`-th worst-case drawdown from
        the backtest's own bootstrap. If `session` is given and the check
        trips, routes a halt through `te.risk.killswitch`."""
        if percentile not in self._percentiles:
            raise ValueError(f"envelope has no stored {percentile} percentile — available: {sorted(self._percentiles)}")
        threshold = self._percentiles[percentile]
        breached = current_drawdown_paise < threshold
        if breached and session is not None:
            killswitch.trip(
                session,
                f"Tier 2 drawdown envelope hard halt: current drawdown {current_drawdown_paise}p exceeds "
                f"the stored {percentile:.0%} bootstrapped envelope ({threshold}p)",
            )
        return breached


# ---------------------------------------------------------------------------
# Tier 3 — rolling performance (informational only)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RollingPerformanceSnapshot:
    """Purely descriptive — there is deliberately no field or method on this
    class or `RollingPerformance` that can trigger a halt/throttle. See the
    module + class docstrings."""

    n_sessions: int
    n_trades: int
    trailing_sharpe: float | None
    trailing_profit_factor: float | None


class RollingPerformance:
    """Tier 3. Trailing `window_sessions`-session Sharpe and profit factor
    from CLOSED trades — dashboard/human-review signal only. This class
    intentionally exposes no method whose name or return type resembles a
    halt/throttle action (see
    `tests/risk/test_monitors.py::test_rolling_performance_never_halts_alone`)."""

    def __init__(self, session: Session, *, window_sessions: int = 20) -> None:
        self._session = session
        self._window_sessions = window_sessions

    def snapshot(self) -> RollingPerformanceSnapshot:
        # Resolve the window's cutoff DATE in SQL first, then fetch only the
        # trades at/after it — the whole trade history never gets hydrated
        # to compute a 20-session statistic.
        session_dates = recent_session_dates(self._session, limit=self._window_sessions)
        if not session_dates:
            return RollingPerformanceSnapshot(
                n_sessions=0, n_trades=0, trailing_sharpe=None, trailing_profit_factor=None
            )

        cutoff = min(session_dates)
        trailing = trades_closed_since(self._session, cutoff)
        net_pnls = [row.net_pnl_paise for row in trailing]

        return RollingPerformanceSnapshot(
            n_sessions=len(session_dates),
            n_trades=len(trailing),
            trailing_sharpe=sharpe(net_pnls),
            trailing_profit_factor=profit_factor(net_pnls),
        )


__all__ = [
    "DEFAULT_CUSUM_CONFIG",
    "DEFAULT_SLIPPAGE_MIN_OBSERVATIONS",
    "DEFAULT_SLIPPAGE_Z_THRESHOLD",
    "CusumAction",
    "CusumConfig",
    "CusumMonitor",
    "DrawdownEnvelope",
    "RollingPerformance",
    "RollingPerformanceSnapshot",
    "SlippageMonitor",
    "SlippageStatus",
]
