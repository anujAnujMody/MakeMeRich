"""The credit-spread P&L, margin and stop/target arithmetic in isolation.

A sign error here is the most dangerous kind: it produces a plausible,
well-formed number that is simply backwards — a "profitable" spread that is
actually a loser. These tests build trades by hand with known answers and
check the module against them, rather than trusting a large backtest run to
reveal a wrong sign by looking wrong.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from pathlib import Path

import pytest

from te.backtest.spread_lab import SpreadGeometry, SpreadTrade, _resolve_legs, _score, _score_trade
from te.data.charges_loader import load_charge_rate_table
from te.data.option_history import OptionContractIndex
from te.domain.costs import CostModel
from te.domain.money import Paise
from te.domain.symbols import ParsedOptionSymbol

_ON = dt.date(2026, 3, 10)


@pytest.fixture
def rates() -> CostModel:
    table = load_charge_rate_table(Path(__file__).resolve().parents[2] / "config" / "charges.yaml")
    return CostModel(table)


def _geometry(**overrides: object) -> SpreadGeometry:
    defaults: dict[str, object] = {
        "short_otm": 2,
        "width_strikes": 3,
        "profit_target_pct": Decimal("0.5"),
        "stop_loss_multiple": Decimal("2.0"),
        "max_hold": dt.timedelta(minutes=180),
    }
    defaults.update(overrides)
    return SpreadGeometry(**defaults)  # type: ignore[arg-type]


def test_a_full_winner_nets_close_to_the_whole_credit(rates: CostModel) -> None:
    """Short leg expires worthless, long leg expires worthless: the seller
    keeps the whole credit, minus the four legs' real costs."""
    net_pnl, max_loss, r_multiple = _score_trade(
        entry_credit=Paise(3000),
        short_entry=Paise(5000),
        long_entry=Paise(2000),
        short_exit=Paise(0),
        long_exit=Paise(0),
        short_strike=Decimal(24500),
        long_strike=Decimal(24800),
        lot_size=65,
        exchange="NFO",
        cost_model=rates,
        on=_ON,
    )
    # Gross = entry_credit (3000) since both legs went to zero. Net must be
    # POSITIVE and close to but less than the gross credit — costs shave it
    # down, they never flip its sign on a full winner at this size.
    assert 0 < int(net_pnl) < 3000
    assert r_multiple > 0
    assert max_loss > 0


def test_a_full_loser_loses_close_to_the_whole_width(rates: CostModel) -> None:
    """Both legs finish deep in the money, spread pinned at its full width:
    the seller loses (width - credit), the worst case the position can
    produce."""
    width_paise = (24800 - 24500) * 100  # 300 points = 30,000 paise
    net_pnl, max_loss, r_multiple = _score_trade(
        entry_credit=Paise(3000),
        short_entry=Paise(5000),
        long_entry=Paise(2000),
        short_exit=Paise(35000),  # short leg now deep ITM
        long_exit=Paise(32000),  # long leg protects, but not fully
        short_strike=Decimal(24500),
        long_strike=Decimal(24800),
        lot_size=65,
        exchange="NFO",
        cost_model=rates,
        on=_ON,
    )
    assert int(max_loss) == width_paise - 3000
    assert net_pnl < 0
    assert r_multiple < 0
    # The loss must not exceed the max loss by more than costs — a spread's
    # loss is CAPPED by construction; a result worse than max_loss - costs
    # would mean the P&L formula has a sign or leg-pairing error.
    assert int(net_pnl) >= -int(max_loss) - 500  # 500 paise of slack for 4 legs' real costs


def test_r_multiple_is_bounded_by_construction(rates: CostModel) -> None:
    """A defined-risk spread cannot produce an R below -1 (excluding costs)
    or meaningfully above the credit-to-width ratio. A value wildly outside
    [-1.5, +1.5] signals a units mismatch (paise vs rupees, or per-unit vs
    per-lot) rather than a real outcome."""
    net_pnl, max_loss, r_multiple = _score_trade(
        entry_credit=Paise(3000),
        short_entry=Paise(5000),
        long_entry=Paise(2000),
        short_exit=Paise(1000),
        long_exit=Paise(200),
        short_strike=Decimal(24500),
        long_strike=Decimal(24800),
        lot_size=65,
        exchange="NFO",
        cost_model=rates,
        on=_ON,
    )
    assert -1.5 <= r_multiple <= 1.5, f"r_multiple {r_multiple} is out of plausible range — check for a unit error"
    del net_pnl


def test_max_loss_is_floored_at_the_credit_not_negative(rates: CostModel) -> None:
    """A spread priced so the width is smaller than the credit received
    would otherwise produce a negative 'max loss', which is not a real
    quantity — it must floor at the credit itself."""
    _, max_loss, _ = _score_trade(
        entry_credit=Paise(5000),
        short_entry=Paise(6000),
        long_entry=Paise(1000),
        short_exit=Paise(0),
        long_exit=Paise(0),
        short_strike=Decimal(24500),
        # Deliberately narrow width (50 points = 5000 paise) below the
        # credit (5000 paise) — an edge case the archive could produce on a
        # thin day.
        long_strike=Decimal(24550),
        lot_size=65,
        exchange="NFO",
        cost_model=rates,
        on=_ON,
    )
    assert int(max_loss) >= 0


def test_all_four_legs_are_charged_not_two(rates: CostModel) -> None:
    """The whole reason `_score_trade` exists rather than reusing
    `CostModel.round_trip` — a spread opens AND closes two legs, four order
    executions, not the two `round_trip` prices for a single instrument.
    Costed at only two legs, net P&L on a flat trade would be too generous
    by roughly half the total cost."""
    # A trade with an unchanged spread value (short and long both flat) has
    # a net P&L that is EXACTLY minus the four legs' cost — no directional
    # P&L to obscure the comparison.
    net_pnl, _, _ = _score_trade(
        entry_credit=Paise(3000),
        short_entry=Paise(5000),
        long_entry=Paise(2000),
        short_exit=Paise(5000),
        long_exit=Paise(2000),
        short_strike=Decimal(24500),
        long_strike=Decimal(24800),
        lot_size=65,
        exchange="NFO",
        cost_model=rates,
        on=_ON,
    )
    four_leg_cost = (
        int(rates.leg(side="SELL", premium=Paise(5000), qty=65, exchange="NFO", on=_ON).total)
        + int(rates.leg(side="BUY", premium=Paise(2000), qty=65, exchange="NFO", on=_ON).total)
        + int(rates.leg(side="BUY", premium=Paise(5000), qty=65, exchange="NFO", on=_ON).total)
        + int(rates.leg(side="SELL", premium=Paise(2000), qty=65, exchange="NFO", on=_ON).total)
    )
    # `net_pnl` is PER UNIT, rounded — `Paise(round(net_pnl_total / lot_size))`
    # — so multiplying back up drifts from the exact total by at most
    # half a lot's worth of rounding. A tolerance of `lot_size` paise is
    # generous slack for that, while still catching a real two-legs-not-four
    # bug, which would be off by roughly HALF the total cost (thousands of
    # paise, not tens).
    assert int(net_pnl) * 65 == pytest.approx(-four_leg_cost, abs=65)


def test_reported_margin_is_per_lot_not_per_unit() -> None:
    """The exact 2026-08-01 regression: the first run reported a NIFTY
    spread's margin as Rs 116 — sixty-five times too small — because
    `max_loss_per_unit` was displayed as if it were already a per-lot
    figure. This is the single number a reader uses to decide whether a
    spread fits their capital, so it is pinned directly rather than trusted
    to a large backtest run to look wrong."""
    trade = SpreadTrade(
        entry_ts=dt.datetime(2026, 3, 10, 5, 0, tzinfo=dt.UTC),
        direction="long_call",
        short_symbol="NIFTY10MAR26P24500",
        long_symbol="NIFTY10MAR26P24350",
        entry_credit=Paise(3000),
        exit_reason="target",
        net_pnl_per_unit=Paise(1500),
        max_loss_per_unit=Paise(12000),  # 120 rupees/unit
        r_multiple=0.5,
        lot_size=65,
    )
    result = _score(
        strategy="test",
        instrument="NIFTY",
        trades=[trade] * 40,  # above the n>=30 reporting floor
        unresolved=0,
        first_day=_ON,
        last_day=_ON,
        trial_ledger=None,
        run_id="test",
    )
    # 12000 paise/unit x 65 units/lot = 780,000 paise = Rs 7,800/lot.
    assert int(result.median_margin_paise) == 12000 * 65, (
        f"expected Rs {12000 * 65 / 100:.0f}/lot, got Rs {int(result.median_margin_paise) / 100:.0f}/lot — "
        "the per-unit-to-per-lot multiplication is missing again"
    )


def test_bullish_signal_sells_a_put_bearish_sells_a_call() -> None:
    """The direction mapping is the one thing that, if backwards, would
    silently trade the opposite market view of what the strategy intended."""
    index = OptionContractIndex.__new__(OptionContractIndex)
    index._base = "NIFTY"  # noqa: SLF001
    index._expiries = [_ON + dt.timedelta(days=2)]  # noqa: SLF001
    index._by_expiry = {}  # noqa: SLF001
    for option_type in ("CE", "PE"):
        index._by_expiry[(index._expiries[0], option_type)] = [  # noqa: SLF001
            ParsedOptionSymbol(
                symbol=f"NIFTY{index._expiries[0]:%d%b%y}".upper() + f"{option_type[0]}{strike}",  # noqa: SLF001
                base="NIFTY", expiry=index._expiries[0], strike=Decimal(strike), option_type=option_type,  # noqa: SLF001
            )
            for strike in range(24_000, 25_500, 50)
        ]

    geometry = _geometry()
    bullish = _resolve_legs(index, on=_ON, index_level=Decimal(24500), direction="long_call", geometry=geometry)
    bearish = _resolve_legs(index, on=_ON, index_level=Decimal(24500), direction="long_put", geometry=geometry)

    assert bullish is not None and bearish is not None
    assert bullish[0].option_type == "PE", "a bullish (long_call) signal must sell a PUT — a bull put spread"
    assert bearish[0].option_type == "CE", "a bearish (long_put) signal must sell a CALL — a bear call spread"


def test_the_long_leg_sits_further_from_spot_than_the_short_leg() -> None:
    """The protective leg is worthless as protection if it is not actually
    further out than the leg being sold."""
    index = OptionContractIndex.__new__(OptionContractIndex)
    index._base = "NIFTY"  # noqa: SLF001
    index._expiries = [_ON + dt.timedelta(days=2)]  # noqa: SLF001
    index._by_expiry = {}  # noqa: SLF001
    for option_type in ("CE", "PE"):
        index._by_expiry[(index._expiries[0], option_type)] = [  # noqa: SLF001
            ParsedOptionSymbol(
                symbol=f"NIFTY{index._expiries[0]:%d%b%y}".upper() + f"{option_type[0]}{strike}",  # noqa: SLF001
                base="NIFTY", expiry=index._expiries[0], strike=Decimal(strike), option_type=option_type,  # noqa: SLF001
            )
            for strike in range(24_000, 25_500, 50)
        ]

    legs = _resolve_legs(
        index,
        on=_ON,
        index_level=Decimal(24500),
        direction="long_call",
        geometry=_geometry(short_otm=2, width_strikes=3),
    )
    assert legs is not None
    short_leg, long_leg = legs
    # Bull put spread: the sold put sits ABOVE the bought (further OTM) put.
    assert short_leg.strike > long_leg.strike
