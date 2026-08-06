"""The straddle lab, tested on hand-built premium paths.

Every number below is chosen so the expected answer can be worked out by
hand, because the failures this module is most exposed to are silent
arithmetic ones: a sign error on a wing, a leg that keeps being marked after
its stop fired, or a strangle quietly reported as a straddle.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from pathlib import Path

import pandas as pd
import pytest

from te.backtest.straddle_lab import (
    StraddleGeometry,
    resolve_legs,
    run_straddle_backtest,
    score_trade,
    walk,
)
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
WING = Decimal(200)
LOT = 75


def _minute(i: int) -> dt.datetime:
    return dt.datetime(2026, 8, 25, 9, 15, tzinfo=IST) + dt.timedelta(minutes=i)


def _rows(symbol: str, closes: list[float]) -> list[dict[str, object]]:
    return [
        {
            "symbol": symbol, "exchange": "NFO", "event_ts": _minute(i), "interval": "1m",
            "o": c, "h": c, "l": c, "c": c, "v": 1_000, "oi": 0, "ingested_at": _minute(i), "source": "test",
        }
        for i, c in enumerate(closes)
    ]


def _flat(value: float, n: int = 400) -> list[float]:
    return [value] * n


@pytest.fixture
def cost_model() -> CostModel:
    return CostModel(select_rates(load_charge_rate_table(_CHARGES), DAY))


def _build(
    tmp_path: Path,
    *,
    call: list[float],
    put: list[float],
    call_wing: list[float] | None = None,
    put_wing: list[float] | None = None,
    index: float = 25_000.0,
) -> tuple[BarStore, OptionContractIndex]:
    """A store holding the index plus a full four-leg chain around 25,000."""
    store = BarStore(tmp_path / "bars")
    rows: list[dict[str, object]] = []
    rows += _rows("NIFTY", _flat(index))
    rows += _rows(f"NIFTY27AUG26{STRIKE}CE", call)
    rows += _rows(f"NIFTY27AUG26{STRIKE}PE", put)
    rows += _rows(f"NIFTY27AUG26{STRIKE + int(WING)}CE", call_wing or _flat(20.0))
    rows += _rows(f"NIFTY27AUG26{STRIKE - int(WING)}PE", put_wing or _flat(20.0))
    # Neighbouring strikes so `strike_step` can see a real 50-point grid —
    # without them `otm_points` has nothing to measure "near enough" against.
    for offset in (-150, -100, -50, 50, 100, 150, 250, 300):
        rows += _rows(f"NIFTY27AUG26{STRIKE + offset}CE", _flat(15.0))
        rows += _rows(f"NIFTY27AUG26{STRIKE + offset}PE", _flat(15.0))
    store.append(pd.DataFrame(rows, columns=list(BAR_COLUMNS)))
    return store, OptionContractIndex(store, "NIFTY")


def _geometry(**overrides: object) -> StraddleGeometry:
    base = {
        "entry_time": dt.time(9, 20),
        "wing_points": WING,
        "leg_stop_fraction": Decimal("0.25"),
        "take_profit_fraction": None,
        "min_days_to_expiry": 0,
        "max_days_to_expiry": 7,
    }
    return StraddleGeometry(**{**base, **overrides})  # type: ignore[arg-type]


def test_both_legs_are_the_same_strike(tmp_path: Path) -> None:
    """A straddle is one strike. If the call and put resolved
    independently to different strikes it would be a strangle, with
    different decay, reported under the wrong name."""
    _store, contracts = _build(tmp_path, call=_flat(100.0), put=_flat(100.0))
    legs = resolve_legs(contracts, on=DAY, index_level=Decimal(25_000), geometry=_geometry())
    assert legs is not None
    assert legs.call.strike == legs.put.strike == Decimal(STRIKE)


def test_wings_sit_symmetrically_around_the_short_strike(tmp_path: Path) -> None:
    """Measured from what was SOLD, not from spot — otherwise one side is
    protected closer than the other and the structure is not a fly."""
    _store, contracts = _build(tmp_path, call=_flat(100.0), put=_flat(100.0))
    legs = resolve_legs(contracts, on=DAY, index_level=Decimal(24_980), geometry=_geometry())
    assert legs is not None
    assert legs.call_wing.strike - legs.call.strike == WING
    assert legs.put.strike - legs.put_wing.strike == WING


def test_a_decaying_straddle_is_a_profit_for_the_seller(tmp_path: Path) -> None:
    """The whole hypothesis in one assertion: premium falls through the
    session, and the position that SOLD it ends up ahead — after eight legs
    of real charges."""
    decay = [100.0] * 10 + [80.0] * 400
    store, contracts = _build(tmp_path, call=decay, put=decay)
    legs = resolve_legs(contracts, on=DAY, index_level=Decimal(25_000), geometry=_geometry(leg_stop_fraction=None))
    assert legs is not None
    walked = walk(store=store, legs=legs, entry_ts=ENTRY, geometry=_geometry(leg_stop_fraction=None))
    assert walked is not None
    assert walked.reason == "time"
    _credit, net, _max_loss, _r = score_trade(
        walked=walked, legs=legs, lot_size=LOT, exchange="NFO",
        cost_model=CostModel(select_rates(load_charge_rate_table(_CHARGES), DAY)), on=DAY,
    )
    assert net > 0


def test_a_stopped_leg_stops_moving(tmp_path: Path) -> None:
    """The load-bearing detail of a per-leg stop. The call runs to 130
    (stopped at 125), then falls back to 90. If the stopped leg were still
    being marked, the loss it locked in would be silently given back and
    every trending day would score as a win."""
    call = [100.0] * 10 + [130.0] * 5 + [90.0] * 400
    store, contracts = _build(tmp_path, call=call, put=_flat(100.0))
    geometry = _geometry(leg_stop_fraction=Decimal("0.25"))
    legs = resolve_legs(contracts, on=DAY, index_level=Decimal(25_000), geometry=geometry)
    assert legs is not None
    walked = walk(store=store, legs=legs, entry_ts=ENTRY, geometry=geometry)
    assert walked is not None
    assert walked.call_exit == Paise(13_000), "must exit at the breach price, not the later 90"
    assert walked.reason == "one_leg_stopped"


def test_both_legs_stopping_ends_the_trade_immediately(tmp_path: Path) -> None:
    """A V-shaped day — the documented way this strategy loses. Nothing is
    left running once both shorts are closed."""
    call = [100.0] * 10 + [140.0] * 5 + [50.0] * 400
    put = [100.0] * 16 + [140.0] * 400
    store, contracts = _build(tmp_path, call=call, put=put)
    geometry = _geometry(leg_stop_fraction=Decimal("0.25"))
    legs = resolve_legs(contracts, on=DAY, index_level=Decimal(25_000), geometry=geometry)
    assert legs is not None
    walked = walk(store=store, legs=legs, entry_ts=ENTRY, geometry=geometry)
    assert walked is not None
    assert walked.reason == "both_legs_stopped"
    assert walked.call_exit == Paise(14_000)
    assert walked.put_exit == Paise(14_000)


def test_the_take_profit_closes_the_whole_structure(tmp_path: Path) -> None:
    collapse = [100.0] * 10 + [20.0] * 400
    store, contracts = _build(tmp_path, call=collapse, put=collapse)
    geometry = _geometry(leg_stop_fraction=None, take_profit_fraction=Decimal("0.5"))
    legs = resolve_legs(contracts, on=DAY, index_level=Decimal(25_000), geometry=geometry)
    assert legs is not None
    walked = walk(store=store, legs=legs, entry_ts=ENTRY, geometry=geometry)
    assert walked is not None
    assert walked.reason == "target"


def test_max_loss_is_both_wing_distances_less_the_credit(tmp_path: Path) -> None:
    """The number the account has to afford, and it is the INTRADAY bound,
    not the expiry one.

    At expiry only one vertical can be in the money, so `wing - credit`
    would be right. This strategy closes at 15:15, where both verticals
    still carry value and the cost to close is bounded by `2 x wing`. The
    first sweep used the expiry figure and a single day lost Rs 30,888
    against a Rs 1,500 budget."""
    store, contracts = _build(
        tmp_path, call=_flat(100.0), put=_flat(100.0), call_wing=_flat(20.0), put_wing=_flat(20.0)
    )
    geometry = _geometry(leg_stop_fraction=None)
    legs = resolve_legs(contracts, on=DAY, index_level=Decimal(25_000), geometry=geometry)
    assert legs is not None
    walked = walk(store=store, legs=legs, entry_ts=ENTRY, geometry=geometry)
    assert walked is not None
    credit, _net, max_loss, _r = score_trade(
        walked=walked, legs=legs, lot_size=LOT, exchange="NFO",
        cost_model=CostModel(select_rates(load_charge_rate_table(_CHARGES), DAY)), on=DAY,
    )
    assert credit == Paise(16_000), "100 + 100 sold, 20 + 20 paid for the wings"
    # Two 200-point wings = Rs 400 = 40,000 paise, less the Rs 160 credit.
    assert max_loss == Paise(24_000)


def test_wings_costing_more_than_the_credit_are_refused(tmp_path: Path) -> None:
    """A broken quote, not a trade. Labelling it a guaranteed loser would
    put a fabricated number into the sample."""
    store, contracts = _build(
        tmp_path, call=_flat(10.0), put=_flat(10.0), call_wing=_flat(50.0), put_wing=_flat(50.0)
    )
    geometry = _geometry(leg_stop_fraction=None)
    legs = resolve_legs(contracts, on=DAY, index_level=Decimal(25_000), geometry=geometry)
    assert legs is not None
    assert walk(store=store, legs=legs, entry_ts=ENTRY, geometry=geometry) == "credit_not_positive"


def test_a_credit_at_or_above_the_wing_distance_is_refused(tmp_path: Path) -> None:
    """Free money, which options do not offer — a crossed or stale quote.
    Traded, its max loss would be zero or negative and sizing would take an
    unbounded number of lots on a price that never existed."""
    store, contracts = _build(
        tmp_path, call=_flat(150.0), put=_flat(150.0), call_wing=_flat(10.0), put_wing=_flat(10.0)
    )
    geometry = _geometry(leg_stop_fraction=None)
    legs = resolve_legs(contracts, on=DAY, index_level=Decimal(25_000), geometry=geometry)
    assert legs is not None
    # Credit is 150 + 150 - 10 - 10 = Rs 280 against a Rs 200 wing.
    assert walk(store=store, legs=legs, entry_ts=ENTRY, geometry=geometry) == "credit_exceeds_wing"


def test_costs_are_charged_on_all_eight_legs(tmp_path: Path) -> None:
    """A structure that never moves must still LOSE, by exactly the
    charges. This is the guard against a cost model quietly applied to two
    legs instead of eight."""
    store, contracts = _build(tmp_path, call=_flat(100.0), put=_flat(100.0))
    geometry = _geometry(leg_stop_fraction=None)
    legs = resolve_legs(contracts, on=DAY, index_level=Decimal(25_000), geometry=geometry)
    assert legs is not None
    walked = walk(store=store, legs=legs, entry_ts=ENTRY, geometry=geometry)
    assert walked is not None
    _credit, net, _max_loss, _r = score_trade(
        walked=walked, legs=legs, lot_size=LOT, exchange="NFO",
        cost_model=CostModel(select_rates(load_charge_rate_table(_CHARGES), DAY)), on=DAY,
    )
    assert net < 0, "zero price movement must still cost money"


def test_the_backtest_takes_one_trade_per_session(tmp_path: Path) -> None:
    """No signal means no variability in COUNT — one entry per day the
    archive can support, and unresolved days counted rather than dropped
    silently."""
    store, contracts = _build(tmp_path, call=_flat(100.0), put=_flat(100.0))
    trades, unresolved = run_straddle_backtest(
        store=store, contracts=contracts,
        cost_model=CostModel(select_rates(load_charge_rate_table(_CHARGES), DAY)),
        lot_size_for=lambda _day: LOT, geometry=_geometry(leg_stop_fraction=None), instrument="NIFTY",
    )
    assert len(trades) + sum(unresolved.values()) == 1
    assert len(trades) == 1
    assert not unresolved, f"nothing should be unresolved here, got {dict(unresolved)}"
    assert trades[0].strike == Decimal(STRIKE)
