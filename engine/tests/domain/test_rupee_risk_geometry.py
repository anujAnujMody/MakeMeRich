"""A rupee loss cap that actually caps the loss in rupees.

Asked for by the owner on 2026-08-06, after `NIFTY11AUG2624650CE` lost
Rs 2,272 on a Rs 50,000 account in a single trade — 4.5% of the account —
under a 20% premium stop that had been sized entirely correctly.

The request was simple and neither existing geometry could express it: "buy
whatever the signal picks, but cap my loss at Rs 600-700". The reason it
could not be expressed is arithmetic, and it is what these tests pin down:

* a PERCENTAGE of premium is a different rupee loss on every contract
* an ABSOLUTE premium distance is a different rupee loss on every lot size

Only a rupee figure divided by the position's own quantity is stable across
both, which is what `RupeeRiskGeometry` computes.

The load-bearing test in this file is
`test_the_cap_is_the_same_rupees_across_instruments` — it is the property
the owner actually asked for, and the one both older geometries fail.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest

from te.domain.costs import ChargeRates, CostModel
from te.domain.geometry import (
    AbsolutePointGeometry,
    DegenerateGeometry,
    PremiumPercentGeometry,
    RupeeRiskGeometry,
)
from te.domain.money import Paise

#: Real quotes, 2026-08-06. Premium in paise, then the lot size SEBI set for
#: that index in January 2026 — deliberately all different, because the whole
#: point is that a single rupee figure has to survive both varying.
NIFTY = (Paise(16_680), 65)
SENSEX = (Paise(28_380), 20)
BANKNIFTY = (Paise(76_655), 30)

RS_700 = 70_000


def _geometry(**overrides: object) -> RupeeRiskGeometry:
    base: dict[str, object] = {"max_loss_paise": RS_700, "target_multiple": Decimal(10)}
    return RupeeRiskGeometry(**{**base, **overrides})  # type: ignore[arg-type]


def _loss_rupees(premium: Paise, lot_size: int, geometry: RupeeRiskGeometry, *, lots: int = 1) -> float:
    levels = geometry.levels(premium, quantity=lot_size * lots)
    return (int(premium) - int(levels.stop)) * lot_size * lots / 100


def test_the_cap_is_the_same_rupees_across_instruments() -> None:
    """THE property. One number, three instruments with three different lot
    sizes and three very different premiums, one rupee loss.

    Rs 700 // quantity truncates, so the realised loss lands at or just under
    the cap — never above it. The tolerance below is one lot-size worth of
    truncation, not a fudge factor.
    """
    geometry = _geometry()
    for premium, lot_size in (NIFTY, SENSEX, BANKNIFTY):
        loss = _loss_rupees(premium, lot_size, geometry)
        assert 700 - lot_size / 100 <= loss <= 700, f"lot {lot_size}: lost Rs {loss} against a Rs 700 cap"


def test_cost_estimator_keeps_the_true_net_loss_under_the_cap() -> None:
    """The cap is GROSS unless a `cost_estimator` is supplied — see the class
    docstring's "The cap is GROSS unless a cost_estimator is supplied"
    section. Every other test in this file computes loss as a pure price
    distance and never exercises the fixed-point block at
    `geometry.py:318-331`; this is the one that does, using a real
    `CostModel.round_trip`-backed estimator at NIFTY's live premium/quantity
    and today's real charge rates (`config/charges.yaml`'s 2026-04-01 row)."""
    rates = ChargeRates(
        effective_from=dt.date(2026, 4, 1),
        verified_at=dt.date(2026, 7, 29),
        brokerage_per_executed_order_paise=Paise(2000),
        stt_sell_bps=Decimal("15.0"),
        stt_exercise_intrinsic_bps=Decimal("15.0"),
        exchange_txn_bps={"NFO": Decimal("3.553"), "BFO": Decimal("3.25")},
        sebi_bps=Decimal("0.01"),
        gst_pct=Decimal("18.0"),
        stamp_buy_bps=Decimal("0.3"),
    )
    cost_model = CostModel(rates)
    on = dt.date(2026, 8, 6)
    premium, lot_size = NIFTY

    def estimator(exit_premium: Paise) -> Paise:
        breakdown = cost_model.round_trip(
            entry_premium=premium, exit_premium=exit_premium, qty=lot_size, exchange="NFO", on=on
        )
        return Paise(breakdown.total)

    levels = _geometry().levels(premium, quantity=lot_size, cost_estimator=estimator)

    stop_distance = int(premium) - int(levels.stop)
    gross_loss_at_stop = stop_distance * lot_size
    round_trip_costs = int(estimator(levels.stop))
    assert gross_loss_at_stop + round_trip_costs <= RS_700, (
        f"gross {gross_loss_at_stop} + costs {round_trip_costs} breaches the Rs 700 cap"
    )
    # And the cost-aware stop must actually be TIGHTER than the gross-only
    # one, or the fixed point never ran at all.
    gross_only_stop_distance = RS_700 // lot_size
    assert stop_distance < gross_only_stop_distance


def test_a_percentage_stop_cannot_express_this() -> None:
    """Why the new type exists rather than a new percentage. The SAME 20%
    stop that lost Rs 2,272 on NIFTY loses less than half that on SENSEX —
    so no single percentage is a rupee cap.

    Asserted rather than described, because "a percentage is a different
    rupee amount" is exactly the kind of claim that reads as obvious and gets
    the arithmetic backwards.
    """
    percent = PremiumPercentGeometry(stop_pct=Decimal(20), target_pct=Decimal(20))
    losses = {}
    for name, (premium, lot_size) in (("NIFTY", NIFTY), ("SENSEX", SENSEX), ("BANKNIFTY", BANKNIFTY)):
        levels = percent.levels(premium)
        losses[name] = (int(premium) - int(levels.stop)) * lot_size / 100
    assert losses["NIFTY"] == pytest.approx(2_168.4, abs=1)
    assert losses["SENSEX"] == pytest.approx(1_135.2, abs=1)
    assert losses["BANKNIFTY"] == pytest.approx(4_599.3, abs=1)
    assert max(losses.values()) > 4 * min(losses.values()), "a fixed percentage is not a fixed rupee risk"


def test_an_absolute_premium_distance_cannot_express_this_either() -> None:
    """The other half. Rs 10.77 of premium is Rs 700 at NIFTY's lot of 65 and
    Rs 215 at SENSEX's 20 — the same distance, a third of the risk."""
    absolute = AbsolutePointGeometry(stop_distance=Paise(1_077), target_distance=Paise(10_770))
    nifty_premium, nifty_lot = NIFTY
    sensex_premium, sensex_lot = SENSEX
    nifty_loss = (int(nifty_premium) - int(absolute.levels(nifty_premium).stop)) * nifty_lot / 100
    sensex_loss = (int(sensex_premium) - int(absolute.levels(sensex_premium).stop)) * sensex_lot / 100
    assert nifty_loss == pytest.approx(700, abs=1)
    assert sensex_loss == pytest.approx(215, abs=1)


def test_more_lots_do_not_multiply_the_loss() -> None:
    """The trap this whole feature turns on.

    Sizing computes `lots = risk_budget // risk_per_lot`, so a SMALLER
    per-lot risk buys MORE lots. If the rupee cap were applied per lot, a
    Rs 700 stop against a Rs 2,500 budget would buy three lots and lose
    Rs 2,100 — a setting that reads as Rs 700 and delivers three times it.

    Here the cap is spread across the whole position, so the answer is
    Rs 700 whatever the lot count. `te.risk.sizing.size_position`'s
    `max_lots` is the other half of the guarantee; this is the geometry
    half.
    """
    geometry = _geometry()
    premium, lot_size = NIFTY
    for lots in (1, 2, 3, 5):
        loss = _loss_rupees(premium, lot_size, geometry, lots=lots)
        assert 700 - (lot_size * lots) / 100 <= loss <= 700, f"{lots} lots lost Rs {loss}"


def test_the_target_is_a_multiple_of_the_risk_not_a_percentage_of_premium() -> None:
    """The profit-cap fix. A 20% stop against a 20% target is 1:1 — every
    winner cut at exactly the size of every loser, a ceiling imposed by
    configuration rather than by the market.

    At 10x the target sits far enough away that the trade is effectively
    uncapped for this account, while still being a real reachable price:
    +65% of premium here, which intraday options do print.
    """
    premium, lot_size = NIFTY
    levels = _geometry().levels(premium, quantity=lot_size)
    risk = int(premium) - int(levels.stop)
    reward = int(levels.target) - int(premium)
    assert reward == 10 * risk
    assert reward * lot_size / 100 == pytest.approx(7_000, abs=10)
    # And it is a genuine price, not a sentinel — see the settings comment on
    # `paper_cycle_target_risk_multiple` for why that distinction matters.
    assert int(levels.target) / int(premium) < 2.0, "a target above +100% of premium would rarely be real"


def test_quantity_is_required() -> None:
    """The conversion is meaningless without it, so it must fail loudly
    rather than assume a lot size. Both older variants ignore `quantity`, so
    forgetting it would otherwise silently produce whatever the default
    happened to be."""
    with pytest.raises(ValueError, match="needs a positive quantity"):
        _geometry().levels(Paise(16_680))
    with pytest.raises(ValueError, match="needs a positive quantity"):
        _geometry().levels(Paise(16_680), quantity=0)


def test_a_premium_entirely_inside_the_cap_is_refused_not_clamped() -> None:
    """A rupee cap larger than the whole position's value used to clamp the
    stop to a premium of ZERO and call that a working trade. It is not one.

    An option cannot print zero, so a stop there can never be hit — and the
    target is computed as a multiple of the same clamped distance, landing
    at roughly 11x entry, which is not reachable either. The position had no
    working stop and no reachable target and could only ever leave on the
    time exit, while every log line and every dashboard field described a
    normally-protected trade.

    The band is real at the live configuration, not hypothetical: with a
    Rs 700 cap over 65 units, any premium from Rs 5.00 (the liquidity
    floor) to Rs 10.76 lands inside it.

    Refusing is what `te.engine.cycle` turns into an honest skip, so the
    signal is passed over with a stated reason rather than traded blind."""
    with pytest.raises(DegenerateGeometry):
        _geometry(max_loss_paise=10_000_00).levels(Paise(5_000), quantity=65)


def test_a_premium_exactly_at_the_cap_boundary_is_refused() -> None:
    """The exact boundary the class docstring names: at a Rs 700 cap over 65
    units, `70_000 // 65 == 1_076` exactly. A premium of Rs 10.76 (1,076p) has
    a stop distance equal to the premium itself — the degenerate "stop at
    zero" case — and must raise, not clamp. One paise higher, at Rs 10.77
    (1,077p), the stop is reachable and the trade works normally."""
    with pytest.raises(DegenerateGeometry):
        _geometry().levels(Paise(1_076), quantity=65)

    levels = _geometry().levels(Paise(1_077), quantity=65)
    assert int(levels.stop) > 0
    assert int(levels.target) > int(levels.stop)


def test_a_cap_too_small_to_express_is_refused() -> None:
    """Rs 0.50 across 6,500 units is less than one paise per unit. There is
    no stop that expresses it, so this raises rather than flooring to zero
    distance and producing a stop AT the entry price — which would exit
    instantly on the first tick and look like a working rule."""
    with pytest.raises(ValueError, match="less than one paise per unit"):
        _geometry(max_loss_paise=50).levels(Paise(16_680), quantity=6_500)


def test_the_profit_lock_survives_the_new_geometry() -> None:
    """It was re-enabled on 2026-08-06 at the 15%/5% values backtested
    against 1,305 real NIFTY ORB trades, and switching geometry must not
    quietly drop it — the fields exist on `ExitLevels`, not on the geometry,
    precisely so every variant can carry them."""
    premium, lot_size = NIFTY
    levels = _geometry(
        profit_lock_activation_pct=Decimal(15), profit_lock_buffer_pct=Decimal(5)
    ).levels(premium, quantity=lot_size)
    assert levels.profit_lock_activation == Paise(int(premium) + int(premium) * 15 // 100)
    assert levels.profit_lock_buffer_pct == Decimal(5)


def test_the_fixed_point_prices_the_exit_below_entry_not_above() -> None:
    """The fixed-point loop at `geometry.py:326-331` must evaluate the cost
    estimator at a candidate BELOW entry (a long position's stop), not
    above it. `test_cost_estimator_keeps_the_true_net_loss_under_the_cap`
    uses a real, nearly-symmetric `CostModel.round_trip` and cannot tell the
    two apart; this estimator is deliberately asymmetric around entry so the
    direction is load-bearing."""
    premium, lot_size = NIFTY

    def estimator(exit_premium: Paise) -> Paise:
        return Paise(0) if int(exit_premium) < int(premium) else Paise(1_000_000)

    levels = _geometry().levels(premium, quantity=lot_size, cost_estimator=estimator)
    assert levels.stop == Paise(int(premium) - (RS_700 // lot_size))


def test_profit_lock_buffer_pct_of_exactly_zero_is_refused() -> None:
    """The boundary `(0, 100)` names at its lower end — `0` itself must be
    refused, not just negative values. Every other test in this file that
    sets `profit_lock_buffer_pct` uses `Decimal(5)`, so nothing exercised
    this exact edge."""
    with pytest.raises(ValueError, match="profit_lock_buffer_pct must be in"):
        _geometry(profit_lock_activation_pct=Decimal(15), profit_lock_buffer_pct=Decimal(0))


def test_a_lock_that_could_never_fire_is_reported_as_off() -> None:
    """Found 2026-08-06 by `scripts/stop_level_report.py`, which crashed on
    `ExitPlan.__post_init__` at a Rs 300 stop:

        profit_lock_activation (37622) must sit between stop (32315) and
        target (36715)

    The lock is a percentage of PREMIUM; the target is a multiple of a RUPEE
    risk. They scale differently, so a small enough rupee stop pulls the
    target in NEARER than the +15% trigger — and a lock the target pre-empts
    can never fire once.

    That validator was right to refuse the plan. The bug was building one.
    Reporting the rule as off is both the honest answer (`honest-metrics`
    forbids a rule that reads as enabled and cannot fire) and the safe one:
    the alternative is a ValueError inside the entry cycle at the exact
    moment a position was meant to open.

    Live is under this threshold today only because the 50%-of-capital cap
    keeps notional below Rs 25,000 against a Rs 46,667 limit — the position
    cap protecting a different rule by coincidence. At Rs 1,00,000 of capital
    that cap moves to Rs 50,000 and a real signal crosses it.
    """
    premium, lot_size = Paise(32_715), 65
    levels = _geometry(
        max_loss_paise=30_000,  # Rs 300 — the stop that crashed the sweep
        profit_lock_activation_pct=Decimal(15),
        profit_lock_buffer_pct=Decimal(5),
    ).levels(premium, quantity=lot_size)

    assert int(premium) + int(premium) * 15 // 100 >= int(levels.target), (
        "premise: at this stop the +15% trigger sits at or beyond the target"
    )
    assert levels.profit_lock_activation is None
    assert levels.profit_lock_buffer_pct is None, "both halves off together, or ExitPlan rejects the pair"


def test_the_lock_survives_whenever_it_can_actually_fire() -> None:
    """The guard above must not quietly disable the rule in the normal case —
    that would be the same class of bug in the opposite direction, and it is
    the case every live trade takes."""
    premium, lot_size = NIFTY
    levels = _geometry(
        profit_lock_activation_pct=Decimal(15), profit_lock_buffer_pct=Decimal(5)
    ).levels(premium, quantity=lot_size)
    assert levels.profit_lock_activation is not None
    assert int(levels.profit_lock_activation) < int(levels.target)


def test_a_lock_at_the_exact_equality_point_is_reported_as_off() -> None:
    """The boundary the guard above is never tested at: `candidate ==
    target` exactly, not past it. `int(candidate) < int(target)` is the
    correct direction for "reachable" — `<=` would treat exact equality as
    reachable and hand `ExitPlan.__post_init__` an activation that sits ON
    the target rather than strictly before it, tripping its strict
    `stop < activation < target` validator at the moment a position was
    meant to open."""
    geometry = RupeeRiskGeometry(
        max_loss_paise=10_000,  # Rs 100
        target_multiple=Decimal(1),
        profit_lock_activation_pct=Decimal(15),
        profit_lock_buffer_pct=Decimal(5),
    )
    premium, lot_size = Paise(670), 100
    levels = geometry.levels(premium, quantity=lot_size)

    candidate = int(premium) + int(premium) * 15 // 100
    assert candidate == int(levels.target), "premise: the +15% trigger sits exactly on the target"
    assert levels.profit_lock_activation is None
    assert levels.profit_lock_buffer_pct is None, "both halves off together, or ExitPlan rejects the pair"


@pytest.mark.parametrize(
    ("kwargs", "match"),
    [
        ({"max_loss_paise": 0}, "max_loss_paise must be positive"),
        ({"max_loss_paise": -1}, "max_loss_paise must be positive"),
        ({"target_multiple": Decimal(0)}, "target_multiple must be positive"),
        ({"trailing_pct": Decimal(0)}, "trailing_pct must be positive"),
        ({"profit_lock_activation_pct": Decimal(15)}, "must be set together"),
        ({"profit_lock_buffer_pct": Decimal(5)}, "must be set together"),
    ],
)
def test_invalid_configurations_are_refused_at_construction(kwargs: dict[str, object], match: str) -> None:
    with pytest.raises(ValueError, match=match):
        _geometry(**kwargs)
