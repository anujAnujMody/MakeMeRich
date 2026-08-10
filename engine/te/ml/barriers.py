"""Converting option-premium exit barriers into INDEX-point distances.

Labels are walked over INDEX bars, because there is no intraday option
history to walk: verified against the live OpenAlgo instance on 2026-07-31,
`history` on an expired option symbol returns `Symbol 'NIFTY30SEP2524500CE'
not found` — an option's candles disappear with the contract. The only
historical option data is the daily bhavcopy OHLC in `option_bhav`, which
cannot say what a contract was worth at 10:47. Index 1-minute bars, by
contrast, are complete for the whole period.

So a barrier expressed as a percentage of PREMIUM has to be restated as an
INDEX move before it can be walked:

    index_move = (pct x ATM_premium) / delta

and likewise the round-trip COST has to be restated in index points before
it can adjust a target. Getting that second conversion wrong is not
theoretical: pricing a round trip on the index LEVEL (~24,400) as though it
were an option premium returned Rs 105.18 per unit and inflated a 73.9-point
target to 179.1 — 2.42x — which, together with an unhandled direction, is
what produced an 8.7% apparent win rate against a ~33% random-walk baseline.

**This lives in `te/` rather than in a script deliberately.** It was briefly
in `scripts/label_replay_firings.py`, imported by four sibling scripts. That
made the project's option->index unit conversion untested (`testpaths` is
`tests/`), unshippable (`root_packages = ["te"]`), and importable only
because `scripts/` happens to resolve as a namespace package. These are the
numbers every downstream result rests on; they belong where the test suite
and the package can see them.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from decimal import Decimal

from te.domain.costs import CostModel
from te.domain.geometry import ExitGeometry
from te.domain.money import Paise


@dataclass(frozen=True)
class AtmSnapshot:
    """One ATM option's measured economics for an underlying.

    Measured from the live chain via `get_option_greeks` on 2026-07-31 (risk
    free 5.5%) — never hand-estimated. Two honest limitations the labels
    inherit, stated where the numbers live:

    * **One IV snapshot stands in for eleven months.** Delta and premium move
      with implied volatility; a high-VIX day in the sample really needed a
      wider index barrier than this assigns.
    * **Theta is ignored.** Over a ~3h hold, theta on a 4-DTE NIFTY ATM is
      roughly 1.5% of premium against a 20% stop — second-order next to
      delta, but it makes these labels very slightly optimistic.
    """

    premium_rupees: float
    delta: float
    spot: float
    days_to_expiry: float
    exchange: str
    lot_size: int


#: The spread here is a FINDING, not noise. BANKNIFTY and BANKEX have no
#: weekly expiry (confirmed against the broker: BANKNIFTY's nearest was 25
#: days out against NIFTY's 4), so their ATM options carry far lower gamma
#: and the same "+40% on the premium" needs a ~1.1% index move against
#: NIFTY's ~0.30%. Labelling all four with one shared barrier would quietly
#: describe four different strategies as one.
ATM_SNAPSHOTS: dict[str, AtmSnapshot] = {
    "NIFTY": AtmSnapshot(92.05, 0.4982, 24_398.0, 4.1, "NFO", 65),
    "BANKNIFTY": AtmSnapshot(876.75, 0.5482, 57_411.5, 25.1, "NFO", 30),
    "SENSEX": AtmSnapshot(476.05, 0.5435, 78_109.15, 6.0, "BFO", 20),
    "BANKEX": AtmSnapshot(1_020.90, 0.5441, 65_125.2, 27.0, "BFO", 30),
}


#: HISTORICAL ONLY — no longer read by `barriers()`. Kept as a display value
#: for scripts that still print "what the barrier sweep found" (see
#: `scripts/label_replay_firings.py`), but they no longer describe what the
#: engine trades.
#:
#: This is the bug this module's `barriers()` used to have: a hardcoded 1:1
#: while `Settings.paper_cycle_max_loss_per_trade_paise` (a real setting,
#: currently Rs 700) makes `te.engine.scheduler._exit_geometry` return a
#: `RupeeRiskGeometry` with `target_multiple=10` for every live trade — a
#: 20%/20% label described a strategy nothing was running. `barriers()` now
#: takes the `ExitGeometry` the caller is actually configured with (see
#: `te.ml.nightly.build_labeled_dataset`) and derives the percentage from it
#: per symbol, since `RupeeRiskGeometry`'s stop distance depends on the
#: position's own premium and quantity and cannot be expressed as one fixed
#: percentage.
STOP_PCT = Decimal(20)
TARGET_PCT = Decimal(20)
MAX_HOLD = dt.timedelta(hours=3)

#: Earliest firing that can be labelled with rates we actually hold.
#:
#: Was 2026-04-01 while `config/charges.yaml` carried a single rate row, and
#: `CostModel` refuses to price a trade predating its earliest row rather
#: than guessing. On 2026-08-01 the three earlier regimes were researched and
#: added (0.0625% STT pre-Oct-2024, the Oct-2024 uniform exchange fee, the
#: Mar-2026 NSE IPFT restructure), so the whole span of the Shoonya option
#: archive is now priceable.
#:
#: Keep this in step with the FIRST row of `config/charges.yaml` — a date
#: earlier than that row will raise, which is the intended failure.
RATES_VERIFIED_FROM = dt.date(2024, 1, 1)


def barriers(
    symbol: str,
    *,
    geometry: ExitGeometry | None = None,
    max_lots: int = 1,
) -> tuple[Paise, Paise]:
    """`index_barriers` at the geometry the engine actually trades.

    `geometry` has no honest default. `RupeeRiskGeometry` — what the live
    engine trades whenever `Settings.paper_cycle_max_loss_per_trade_paise` is
    set, which it is — cannot be expressed as one fixed stop/target
    percentage: its stop distance depends on THIS symbol's own premium and
    quantity (`ATM_SNAPSHOTS[symbol].premium_rupees` and
    `.lot_size * max_lots`). A caller that omits `geometry` gets a `ValueError`
    rather than a silent fall back to a hardcoded 20%/20% — that fallback is
    exactly the bug this signature exists to make impossible to reintroduce
    (see the module-level `STOP_PCT`/`TARGET_PCT` comment).

    `max_lots` must be the same cap `te.risk.sizing.size_position` enforces
    live (`Settings.paper_cycle_max_lots`) — the guarantee that a rupee cap
    stays a rupee cap depends on quantity never growing past it, per
    `RupeeRiskGeometry`'s own docstring.
    """
    if geometry is None:
        raise ValueError(
            f"barriers({symbol!r}) needs the ExitGeometry the engine actually trades — pass one explicitly "
            "(see te.ml.nightly.build_labeled_dataset). There is no honest default: a hardcoded 20%/20% is "
            "the exact bug this parameter exists to prevent."
        )
    stop_pct, target_pct = geometry_barrier_pct(symbol, geometry=geometry, max_lots=max_lots)
    return index_barriers(symbol, stop_pct=stop_pct, target_pct=target_pct)


def geometry_barrier_pct(symbol: str, *, geometry: ExitGeometry, max_lots: int) -> tuple[Decimal, Decimal]:
    """`(stop_pct, target_pct)` of `ATM_SNAPSHOTS[symbol]`'s premium implied
    by `geometry` at `max_lots` — the percentages `barriers()` restates as
    index points, exposed separately so a caller (the nightly training job's
    provenance stamp) can record exactly what a label was computed at without
    re-deriving the number by hand.
    """
    snapshot = ATM_SNAPSHOTS[symbol]
    entry_premium = Paise(int(round(snapshot.premium_rupees * 100)))
    quantity = snapshot.lot_size * max_lots
    # No `cost_estimator` here on purpose. `RupeeRiskGeometry.levels()` would
    # use one to shrink the STOP so the realised NET loss (gross + costs)
    # stays under the cap — correct for a live order, which really does pay
    # costs at the stop. But this module's cost handling is already
    # deliberately one-sided: `te.ml.labeling.label_one_firing` adds cost
    # ONLY to the target ("the plan's formula is stated only for the target
    # side, so only the target barrier is shifted here"), and leaves the stop
    # at its configured, not cost-adjusted, level — `premium_barrier_levels`
    # does the same for the real-premium path. Passing a `cost_estimator`
    # here would tighten the stop derived from geometry AND (independently,
    # downstream, in the walk) push the target out for the same round-trip
    # cost — spending the same cost twice across the two barriers of one
    # sweep, which is not how the rest of this module treats it. Leaving it
    # `None` keeps the stop at the gross distance, consistent with every
    # other barrier this module computes.
    levels = geometry.levels(entry_premium, quantity=quantity)
    stop_pct = Decimal(int(entry_premium) - int(levels.stop)) / Decimal(int(entry_premium)) * 100
    target_pct = Decimal(int(levels.target) - int(entry_premium)) / Decimal(int(entry_premium)) * 100
    return stop_pct, target_pct


def index_barriers(symbol: str, *, stop_pct: Decimal, target_pct: Decimal) -> tuple[Paise, Paise]:
    """Premium-percentage barriers as INDEX-point distances, in paise.

    The bar store holds index levels and `extract_hypothetical_entry_premium`
    reads the index close out of ORB's own recorded condition string, so the
    barrier must be in the same unit: index points x 100.
    """
    snapshot = ATM_SNAPSHOTS[symbol]
    stop_points = float(stop_pct) / 100 * snapshot.premium_rupees / snapshot.delta
    target_points = float(target_pct) / 100 * snapshot.premium_rupees / snapshot.delta
    return Paise(int(round(stop_points * 100))), Paise(int(round(target_points * 100)))


def round_trip_cost_in_index_points(symbol: str, cost_model: CostModel, on: dt.date) -> Paise:
    """The real option round-trip cost, expressed as an INDEX move.

    Price the round trip on the real option notional (premium x lot), divide
    by lot for the per-option-unit cost, then divide by delta, since an
    option unit moves `delta` per index point.
    """
    snapshot = ATM_SNAPSHOTS[symbol]
    premium = Paise(int(round(snapshot.premium_rupees * 100)))
    total = cost_model.round_trip(
        entry_premium=premium, exit_premium=premium, qty=snapshot.lot_size, exchange=snapshot.exchange, on=on
    ).total
    return Paise(int(round(int(total) / snapshot.lot_size / snapshot.delta)))


def barrier_pct_as_index_pct(symbol: str, pct: Decimal) -> float:
    """`pct` of premium, expressed as a percentage move in the INDEX — the
    number that makes the weekly/monthly-expiry difference legible."""
    snapshot = ATM_SNAPSHOTS[symbol]
    points = float(pct) / 100 * snapshot.premium_rupees / snapshot.delta
    return points / snapshot.spot * 100
