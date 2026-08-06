"""VIX-based regime **size throttle**, and nothing else. The plan is
explicit: regime detection here composes (multiplies) against whatever
`te.ml.gates.MLInfluence.size_multiplier` already produces — it never
replaces or bypasses that, and it never generates a trading signal.
Regime-based *parameter switching* ("use different stop/target constants in
different VIX regimes") is explicitly out of scope — the plan calls that
"where overfitting lives".

The rule is deliberately simple and explainable rather than fitted: VIX
terciles computed from recorded history determine a size multiplier —
1.0 in the lowest tercile (calm), reduced in the middle tercile, reduced
further in the top tercile (elevated/crisis vol). No model is trained here;
`vix_terciles()` is a pure percentile computation over whatever history the
caller supplies (there is no persisted VIX history table yet — `te.data.vix`
only fetches a live snapshot — so this module accepts history as an
explicit argument rather than reading a table that doesn't exist)."""

from __future__ import annotations

import statistics
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class RegimeThrottleConfig:
    """Size multipliers by VIX tercile. Defaults are intentionally
    conservative-but-simple, not fitted: full size in calm markets, a
    meaningful haircut in the elevated tercile, half size in the top
    tercile."""

    low_tercile_multiplier: Decimal = Decimal("1.0")
    mid_tercile_multiplier: Decimal = Decimal("0.75")
    high_tercile_multiplier: Decimal = Decimal("0.5")


DEFAULT_REGIME_THROTTLE_CONFIG = RegimeThrottleConfig()


def vix_terciles(history: Sequence[float]) -> tuple[float, float]:
    """Returns `(low/mid boundary, mid/high boundary)` — the 33rd and 66th
    percentiles of `history`, via `statistics.quantiles(n=3)`. Raises if
    `history` is empty (callers must have at least some recorded VIX
    levels; a throttle silently defaulting to "no data" is a footgun for a
    risk control)."""
    if not history:
        raise ValueError("vix_terciles() requires non-empty history")
    if len(history) == 1:
        # A single data point has no meaningful tercile split — both
        # boundaries collapse to that one value.
        only = float(history[0])
        return only, only
    lo, hi = statistics.quantiles(history, n=3, method="inclusive")
    return lo, hi


def regime_size_multiplier(
    current_vix: float,
    history: Sequence[float],
    *,
    config: RegimeThrottleConfig = DEFAULT_REGIME_THROTTLE_CONFIG,
) -> Decimal:
    """The size throttle. `current_vix <= 33rd pct` -> full size; `<= 66th
    pct` -> mid-tercile multiplier; else -> top-tercile multiplier."""
    lo, hi = vix_terciles(history)
    if current_vix <= lo:
        return config.low_tercile_multiplier
    if current_vix <= hi:
        return config.mid_tercile_multiplier
    return config.high_tercile_multiplier


def compose_size_multipliers(regime_multiplier: Decimal, ml_multiplier: Decimal) -> Decimal:
    """The ONLY sanctioned way to combine this module's output with
    `te.ml.gates.MLInfluence.size_multiplier`: multiply them. Neither ever
    replaces the other — a caller (later-phase `te.risk.sizing`/
    `te.engine.cycle`) applies both, always, in this order or the reverse
    (multiplication commutes)."""
    return regime_multiplier * ml_multiplier
