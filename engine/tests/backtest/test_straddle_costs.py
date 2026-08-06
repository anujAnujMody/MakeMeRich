"""Every one of the eight legs is charged, on the right side, at the right
premium.

`test_straddle_lab.py::test_costs_are_charged_on_all_eight_legs` asserted
`net < 0` on a structure that never moved. A mutation audit on 2026-08-05
showed that assertion holds for ANY leg count from one to eight and ANY
side assignment: deleting both wing-entry legs survived, charging all eight
as BUY survived, charging all eight as SELL survived, and costing the exit
legs at the entry premium survived. It constrained exactly one bit — "some
cost was applied".

That is not a pedantic gap. STT on Indian options is charged on the SELL
side on premium, so the sides are not interchangeable: an iron fly is four
sells and four buys at four different premiums, and getting the mix wrong
understates the drag on precisely the structure this module exists to judge.

These tests recompute the expected charge INDEPENDENTLY from `CostModel`
and assert the exact rupee figure, so any change to the leg count, the
sides, or the premiums each leg is priced at fails.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from pathlib import Path

import pandas as pd
import pytest

from te.backtest.straddle_lab import StraddleGeometry, resolve_legs, score_trade, walk
from te.data.barstore import BAR_COLUMNS, BarStore
from te.data.charges_loader import load_charge_rate_table
from te.data.option_history import OptionContractIndex
from te.domain.clock import IST
from te.domain.costs import CostModel, select_rates
from te.domain.money import Paise

_CHARGES = Path(__file__).resolve().parents[2] / "config" / "charges.yaml"
DAY = dt.date(2026, 8, 25)
ENTRY = dt.datetime(2026, 8, 25, 9, 20, tzinfo=IST)
STRIKE = 25_000
WING = 200
LOT = 75

#: Deliberately all DIFFERENT, and deliberately different at exit from
#: entry. Equal premiums would let "costed at the entry price" pass, and
#: equal legs would let a transposition pass.
CALL_ENTRY, PUT_ENTRY, CALL_WING_ENTRY, PUT_WING_ENTRY = 120.0, 90.0, 40.0, 25.0
CALL_EXIT, PUT_EXIT, CALL_WING_EXIT, PUT_WING_EXIT = 60.0, 55.0, 18.0, 11.0


@pytest.fixture
def cost_model() -> CostModel:
    return CostModel(select_rates(load_charge_rate_table(_CHARGES), DAY))


def _series(symbol: str, entry: float, exit_: float) -> list[dict[str, object]]:
    base = dt.datetime(2026, 8, 25, 9, 15, tzinfo=IST)
    closes = [entry] * 10 + [exit_] * 380
    return [
        {
            "symbol": symbol, "exchange": "NFO", "event_ts": base + dt.timedelta(minutes=i),
            "interval": "1m", "o": c, "h": c, "l": c, "c": c, "v": 1_000, "oi": 0,
            "ingested_at": base + dt.timedelta(minutes=i), "source": "test",
        }
        for i, c in enumerate(closes)
    ]


@pytest.fixture
def resolved(tmp_path: Path):  # noqa: ANN201
    store = BarStore(tmp_path / "bars")
    rows: list[dict[str, object]] = []
    rows += _series("NIFTY", float(STRIKE), float(STRIKE))
    rows += _series(f"NIFTY27AUG26{STRIKE}CE", CALL_ENTRY, CALL_EXIT)
    rows += _series(f"NIFTY27AUG26{STRIKE}PE", PUT_ENTRY, PUT_EXIT)
    rows += _series(f"NIFTY27AUG26{STRIKE + WING}CE", CALL_WING_ENTRY, CALL_WING_EXIT)
    rows += _series(f"NIFTY27AUG26{STRIKE - WING}PE", PUT_WING_ENTRY, PUT_WING_EXIT)
    for offset in (-300, -100, 100, 300):
        rows += _series(f"NIFTY27AUG26{STRIKE + offset}CE", 30.0, 30.0)
        rows += _series(f"NIFTY27AUG26{STRIKE + offset}PE", 30.0, 30.0)
    store.append(pd.DataFrame(rows, columns=list(BAR_COLUMNS)))
    contracts = OptionContractIndex(store, "NIFTY")
    geometry = StraddleGeometry(
        entry_time=dt.time(9, 20), wing_points=Decimal(WING), leg_stop_fraction=None,
        take_profit_fraction=None, min_days_to_expiry=0, max_days_to_expiry=7,
    )
    legs = resolve_legs(contracts, on=DAY, index_level=Decimal(STRIKE), geometry=geometry)
    assert not isinstance(legs, str), legs
    walked = walk(store=store, legs=legs, entry_ts=ENTRY, geometry=geometry)
    assert not isinstance(walked, str), walked
    return legs, walked


def _expected_charges(cost_model: CostModel) -> int:
    """The eight legs, spelled out. Written independently of `score_trade`
    on purpose — a helper shared with the implementation would mutate in
    lockstep with it and prove nothing."""
    def leg(side: str, premium: float) -> int:
        return int(
            cost_model.leg(
                side=side, premium=Paise(round(premium * 100)), qty=LOT, exchange="NFO", on=DAY
            ).total
        )

    return (
        leg("SELL", CALL_ENTRY)          # sell the call to open
        + leg("SELL", PUT_ENTRY)         # sell the put to open
        + leg("BUY", CALL_WING_ENTRY)    # buy the call wing to open
        + leg("BUY", PUT_WING_ENTRY)     # buy the put wing to open
        + leg("BUY", CALL_EXIT)          # buy the call back to close
        + leg("BUY", PUT_EXIT)           # buy the put back to close
        + leg("SELL", CALL_WING_EXIT)    # sell the call wing to close
        + leg("SELL", PUT_WING_EXIT)     # sell the put wing to close
    )


def test_the_exact_charge_total_is_applied(resolved, cost_model: CostModel) -> None:  # noqa: ANN001
    """Net = gross - charges, so with gross computed by hand the charge
    total is pinned exactly. Drop a leg, flip a side, or price a leg at the
    wrong premium and this number moves."""
    legs, walked = resolved
    _credit, net, _max_loss, _r = score_trade(
        walked=walked, legs=legs, lot_size=LOT, exchange="NFO", cost_model=cost_model, on=DAY
    )
    gross_per_unit = (
        (CALL_ENTRY - CALL_EXIT)
        + (PUT_ENTRY - PUT_EXIT)
        + (CALL_WING_EXIT - CALL_WING_ENTRY)
        + (PUT_WING_EXIT - PUT_WING_ENTRY)
    )
    gross_total = round(gross_per_unit * 100) * LOT
    expected_net_per_unit = round((gross_total - _expected_charges(cost_model)) / LOT)
    assert int(net) == expected_net_per_unit


def test_the_charge_total_is_not_symmetric_in_side(cost_model: CostModel) -> None:
    """Guards the guard. If BUY and SELL happened to cost the same on this
    charge table, the test above could not detect a side error and would be
    quietly worthless — so assert the asymmetry the STT rule creates."""
    buy = int(cost_model.leg(side="BUY", premium=Paise(12_000), qty=LOT, exchange="NFO", on=DAY).total)
    sell = int(cost_model.leg(side="SELL", premium=Paise(12_000), qty=LOT, exchange="NFO", on=DAY).total)
    assert buy != sell, "sides cost the same here, so no test can detect a side error"


def test_a_structure_that_never_moves_loses_exactly_the_charges(tmp_path: Path, cost_model: CostModel) -> None:
    """The cleanest statement of the same property: zero price movement,
    so net P&L is the negative of the charge total and nothing else."""
    store = BarStore(tmp_path / "bars")
    rows: list[dict[str, object]] = []
    rows += _series("NIFTY", float(STRIKE), float(STRIKE))
    rows += _series(f"NIFTY27AUG26{STRIKE}CE", CALL_ENTRY, CALL_ENTRY)
    rows += _series(f"NIFTY27AUG26{STRIKE}PE", PUT_ENTRY, PUT_ENTRY)
    rows += _series(f"NIFTY27AUG26{STRIKE + WING}CE", CALL_WING_ENTRY, CALL_WING_ENTRY)
    rows += _series(f"NIFTY27AUG26{STRIKE - WING}PE", PUT_WING_ENTRY, PUT_WING_ENTRY)
    for offset in (-300, -100, 100, 300):
        rows += _series(f"NIFTY27AUG26{STRIKE + offset}CE", 30.0, 30.0)
        rows += _series(f"NIFTY27AUG26{STRIKE + offset}PE", 30.0, 30.0)
    store.append(pd.DataFrame(rows, columns=list(BAR_COLUMNS)))
    contracts = OptionContractIndex(store, "NIFTY")
    geometry = StraddleGeometry(
        entry_time=dt.time(9, 20), wing_points=Decimal(WING), leg_stop_fraction=None,
        take_profit_fraction=None, min_days_to_expiry=0, max_days_to_expiry=7,
    )
    legs = resolve_legs(contracts, on=DAY, index_level=Decimal(STRIKE), geometry=geometry)
    assert not isinstance(legs, str), legs
    walked = walk(store=store, legs=legs, entry_ts=ENTRY, geometry=geometry)
    assert not isinstance(walked, str), walked

    _credit, net, _max_loss, _r = score_trade(
        walked=walked, legs=legs, lot_size=LOT, exchange="NFO", cost_model=cost_model, on=DAY
    )
    # Same premiums at entry and exit, so every leg is charged at its own
    # entry price and the four gross terms cancel exactly.
    def leg(side: str, premium: float) -> int:
        return int(
            cost_model.leg(
                side=side, premium=Paise(round(premium * 100)), qty=LOT, exchange="NFO", on=DAY
            ).total
        )

    flat_charges = (
        leg("SELL", CALL_ENTRY) + leg("SELL", PUT_ENTRY)
        + leg("BUY", CALL_WING_ENTRY) + leg("BUY", PUT_WING_ENTRY)
        + leg("BUY", CALL_ENTRY) + leg("BUY", PUT_ENTRY)
        + leg("SELL", CALL_WING_ENTRY) + leg("SELL", PUT_WING_ENTRY)
    )
    assert int(net) == round(-flat_charges / LOT)
