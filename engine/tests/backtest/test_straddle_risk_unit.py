"""The risk unit must bound what a trade can actually lose.

Separate from `test_straddle_lab.py` because this is not a detail of the
walk — it is the number position sizing divides by, and getting it wrong
does not produce a wrong-looking result, it produces a plausible-looking
one with an account-ending drawdown hidden inside it.

The first sweep sized off the EXPIRY bound (`wing - credit`). One cell with
a real sample lost Rs 30,888 in a day against a Rs 1,500 risk budget and
drew down 61.8%. The cause: this strategy closes intraday, where both
verticals of the fly still carry value, so the cost to close is bounded by
`2 x wing` — the expiry bound only holds on the one day the position is
never held to.
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

_CHARGES = Path(__file__).resolve().parents[2] / "config" / "charges.yaml"
DAY = dt.date(2026, 8, 25)
ENTRY = dt.datetime(2026, 8, 25, 9, 20, tzinfo=IST)
STRIKE = 25_000
WING = Decimal(100)
LOT = 75


def _minute(i: int) -> dt.datetime:
    return dt.datetime(2026, 8, 25, 9, 15, tzinfo=IST) + dt.timedelta(minutes=i)


def _series(symbol: str, closes: list[float]) -> list[dict[str, object]]:
    return [
        {
            "symbol": symbol, "exchange": "NFO", "event_ts": _minute(i), "interval": "1m",
            "o": c, "h": c, "l": c, "c": c, "v": 1_000, "oi": 0, "ingested_at": _minute(i), "source": "test",
        }
        for i, c in enumerate(closes)
    ]


@pytest.fixture
def cost_model() -> CostModel:
    return CostModel(select_rates(load_charge_rate_table(_CHARGES), DAY))


def test_a_whipsaw_loss_stays_inside_the_risk_unit(tmp_path: Path, cost_model: CostModel) -> None:
    """The exact shape that broke the first sweep: a 0-DTE-style fly with a
    fat credit against a narrow wing, then a violent move that stops one leg
    while the other is still expensive.

    Expiry arithmetic says almost nothing is at risk here (100-point wing,
    Rs 86 credit -> Rs 14 per unit). The trade below loses far more than
    that, and the assertion is simply that the risk unit the sizing divides
    by is not exceeded.
    """
    store = BarStore(tmp_path / "bars")
    rows: list[dict[str, object]] = []
    rows += _series("NIFTY", [25_000.0] * 400)
    # Straddle sold for 95 + 95 = 190; wings cost 52 each -> credit Rs 86.
    #
    # Then a hard move up, priced ARBITRAGE-CONSISTENTLY (the wings move
    # too — a synthetic path where only the short leg jumps is not a market
    # and would "prove" a bound violation that pricing forbids). The call
    # vertical ends worth 320 - 222 = 98 of its 100 maximum, and the put
    # vertical still carries 18 - 8 = 10 of time value. Cost to close the
    # pair is 108 against an 86 credit: a 22-point loss, which the EXPIRY
    # bound of 14 says is impossible.
    rows += _series(f"NIFTY27AUG26{STRIKE}CE", [95.0] * 10 + [320.0] * 400)
    rows += _series(f"NIFTY27AUG26{STRIKE}PE", [95.0] * 10 + [18.0] * 400)
    rows += _series(f"NIFTY27AUG26{STRIKE + int(WING)}CE", [52.0] * 10 + [222.0] * 400)
    rows += _series(f"NIFTY27AUG26{STRIKE - int(WING)}PE", [52.0] * 10 + [8.0] * 400)
    for offset in (-200, -50, 50, 200):
        rows += _series(f"NIFTY27AUG26{STRIKE + offset}CE", [40.0] * 410)
        rows += _series(f"NIFTY27AUG26{STRIKE + offset}PE", [40.0] * 410)
    store.append(pd.DataFrame(rows, columns=list(BAR_COLUMNS)))
    contracts = OptionContractIndex(store, "NIFTY")

    geometry = StraddleGeometry(
        entry_time=dt.time(9, 20), wing_points=WING, leg_stop_fraction=None,
        take_profit_fraction=None, min_days_to_expiry=0, max_days_to_expiry=7,
    )
    legs = resolve_legs(contracts, on=DAY, index_level=Decimal(STRIKE), geometry=geometry)
    assert legs is not None
    walked = walk(store=store, legs=legs, entry_ts=ENTRY, geometry=geometry)
    assert walked is not None

    credit, net, max_loss, _r = score_trade(
        walked=walked, legs=legs, lot_size=LOT, exchange="NFO", cost_model=cost_model, on=DAY,
    )
    assert int(credit) == 8_600, "95 + 95 sold, 52 + 52 paid"
    assert net < 0
    # The expiry bound would have been 10,000 - 8,600 = 1,400 paise.
    assert abs(int(net)) > 1_400, "this trade genuinely loses more than the expiry bound allows"
    assert abs(int(net)) <= int(max_loss), "the risk unit must bound what the trade actually loses"
