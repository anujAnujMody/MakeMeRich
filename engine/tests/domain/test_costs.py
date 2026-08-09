"""Golden tests for `te/domain/costs.py`.

`test_worked_example` is the safety net for the whole cost engine — it
reproduces the plan's own worked example (buy 1 NIFTY lot @ ₹100, sell @
₹120: gross ₹1,300, costs ₹65.11, net ₹1,234.89) exactly. Any drift here is
either a rate change (update `config/charges.yaml` + this test together) or a
bug — never adjust this test to match whatever the code happens to produce.
"""

import datetime as dt
from decimal import Decimal

import pytest

from te.domain.costs import ChargeRates, CostModel
from te.domain.money import Paise

QTY = 65  # NIFTY lot size as of Jan 2026
EXCHANGE = "NFO"
ON = dt.date(2026, 7, 29)


@pytest.fixture
def rates() -> ChargeRates:
    return ChargeRates(
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


@pytest.fixture
def model(rates: ChargeRates) -> CostModel:
    return CostModel(rates)


def test_worked_example(model: CostModel) -> None:
    entry_premium = Paise(10_000)  # ₹100
    exit_premium = Paise(12_000)  # ₹120
    gross = QTY * (exit_premium - entry_premium)
    assert gross == 130_000  # ₹1,300, matches the plan's worked example

    breakdown = model.round_trip(
        entry_premium=entry_premium, exit_premium=exit_premium, qty=QTY, exchange=EXCHANGE, on=ON
    )

    # Line-by-line arithmetic (paise), rounding each component to the
    # nearest paise (round-half-up) computed off the COMBINED buy+sell
    # notional for the percentage-based charges, then GST off the rounded
    # brokerage/exchange_txn/sebi — this is the convention that reproduces
    # the plan's stated ₹65.11 exactly:
    #   brokerage      = 2000 * 2                                   = 4000
    #   STT (sell only)= 0.15% * (12000 * 65)        = 0.0015*780000= 1170
    #   exchange_txn   = 0.03553% * ((10000+12000)*65)= 0.0003553*1430000 ≈ 508.08 -> 508
    #   sebi           = 0.0001% * 1430000            = 0.000001*1430000  = 1.43   -> 1
    #   gst            = 18% * (4000+508+1) = 18%*4509                    = 811.62 -> 812
    #   stamp (buy only)=0.003% * (10000*65)= 0.00003*650000               = 19.5   -> 20
    #   total = 4000+1170+508+1+812+20 = 6511
    assert breakdown.brokerage == 4000
    assert breakdown.stt == 1170
    assert breakdown.exchange_txn == 508
    assert breakdown.sebi == 1
    assert breakdown.gst == 812
    assert breakdown.stamp == 20
    assert breakdown.total == 6511  # ₹65.11 — the plan's ground truth


def test_worked_example_bfo(model: CostModel) -> None:
    """The BFO twin of `test_worked_example` — SENSEX lot 20, priced at BSE's
    3.25 bps exchange_txn rate rather than NFO's 3.553. Pinned entirely by
    hand so the two exchanges can never collapse into the same number (see
    `round_trip`/`leg`'s `rates.exchange_txn_bps[exchange]` lookup)."""
    qty = 20  # SENSEX lot size
    entry_premium = Paise(10_000)  # ₹100
    exit_premium = Paise(12_000)  # ₹120

    breakdown = model.round_trip(
        entry_premium=entry_premium, exit_premium=exit_premium, qty=qty, exchange="BFO", on=ON
    )
    #   brokerage      = 2000 * 2                                       = 4000
    #   STT (sell only)= 0.15% * (12000 * 20)         = 0.0015*240000   = 360
    #   exchange_txn   = 0.0325% * ((10000+12000)*20) = 0.000325*440000 = 143.0 -> 143
    #   sebi           = 0.0001% * 440000              = 0.000001*440000= 0.044 -> 0
    #   gst            = 18% * (4000+143+0) = 18%*4143                  = 745.74 -> 746
    #   stamp (buy only)=0.003% * (10000*20)=0.00003*200000             = 6.0   -> 6
    #   total = 4000+360+143+0+746+6 = 5255
    assert breakdown.brokerage == 4000
    assert breakdown.stt == 360
    assert breakdown.exchange_txn == 143
    assert breakdown.sebi == 0
    assert breakdown.gst == 746
    assert breakdown.stamp == 6
    assert breakdown.total == 5255


def test_fixed_brokerage_is_regressive(model: CostModel) -> None:
    """At ₹20 premium, brokerage+GST dominates total round-trip cost —
    the exact trap that makes far-cheap strikes a bad idea regardless of
    capital available."""
    premium = Paise(2_000)  # ₹20
    breakdown = model.round_trip(entry_premium=premium, exit_premium=premium, qty=QTY, exchange=EXCHANGE, on=ON)
    assert breakdown.brokerage + breakdown.gst >= Decimal("0.9") * breakdown.total


def test_breakeven_premium_points(model: CostModel) -> None:
    """At ₹100 premium, qty 65, the breakeven exit premium (net P&L == 0)
    should land within a few paise of entry + 97 paise (+0.97 points, per
    the plan)."""
    entry_premium = 10_000

    def net_at(exit_premium: int) -> int:
        breakdown = model.round_trip(
            entry_premium=Paise(entry_premium), exit_premium=Paise(exit_premium), qty=QTY, exchange=EXCHANGE, on=ON
        )
        gross = QTY * (exit_premium - entry_premium)
        return gross - breakdown.total

    # Bisect over integer paise for the smallest exit premium with net >= 0.
    lo, hi = entry_premium, entry_premium + 500
    while lo < hi:
        mid = (lo + hi) // 2
        if net_at(mid) >= 0:
            hi = mid
        else:
            lo = mid + 1

    breakeven_delta_paise = lo - entry_premium
    assert abs(breakeven_delta_paise - 97) <= 5


def test_expiry_settlement_otm_is_zero_stt(model: CostModel) -> None:
    """OTM at expiry: no exercise happens, so no component charges anything."""
    breakdown = model.expiry_settlement(
        strike=Paise(2_500_000), spot_at_expiry=Paise(2_490_000), qty=QTY, option_type="CE", on=ON
    )
    assert breakdown.total == 0
    assert breakdown.stt == 0


def test_expiry_settlement_itm_charges_on_intrinsic_only(model: CostModel) -> None:
    """ITM at expiry: STT is charged on (spot - strike) * qty (intrinsic),
    never on the full settlement notional — the post-Sep-2019 rule."""
    strike = Paise(2_500_000)
    spot = Paise(2_510_000)  # 10,000 paise (₹100) ITM
    breakdown = model.expiry_settlement(strike=strike, spot_at_expiry=spot, qty=QTY, option_type="CE", on=ON)

    intrinsic_notional = (spot - strike) * QTY  # 10,000 * 65 = 650,000 paise
    assert intrinsic_notional == 650_000
    expected_stt = round(650_000 * 15 / 10_000)  # 975.0
    assert breakdown.stt == expected_stt
    assert breakdown.brokerage == 0
    assert breakdown.exchange_txn == 0
    assert breakdown.sebi == 0
    assert breakdown.gst == 0
    assert breakdown.stamp == 0


def test_expiry_settlement_pe_uses_correct_intrinsic_side(model: CostModel) -> None:
    strike = Paise(2_500_000)
    spot = Paise(2_490_000)  # PE is ITM when spot < strike
    breakdown = model.expiry_settlement(strike=strike, spot_at_expiry=spot, qty=QTY, option_type="PE", on=ON)
    intrinsic_notional = (strike - spot) * QTY
    expected_stt = round(intrinsic_notional * 15 / 10_000)
    assert breakdown.stt == expected_stt
    assert breakdown.stt > 0
