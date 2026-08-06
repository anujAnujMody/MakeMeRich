"""The buying lab must trade the expiry band it was asked for.

Until 2026-08-05 it asked the archive for the nearest contract within 7 days
and took whatever came back, so every buying result ever produced pooled
expiry-day firings with six-days-out ones. Those are different instruments:
measured on the real trade history the same NIFTY lot cost Rs 2,746 on its
expiry day and Rs 8,976 six days out.

The credit-spread lab APPEARED to control this from 2026-08-02 but did not:
it applied the bound after asking for the nearest contract, and `nearest`
stops at the first expiry inside the upper bound, so a later band mostly
selected nothing at all. Both labs were fixed on 2026-08-05 by moving the
bound inside `nearest`. These tests pin it down at the level that actually
picks the contract, so a band cannot silently widen — or silently empty —
again.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from pathlib import Path

import pandas as pd
import pytest

from te.backtest.strategy_lab import _label
from te.data.barstore import BAR_COLUMNS, BarStore
from te.data.charges_loader import load_charge_rate_table
from te.data.option_history import OptionContractIndex
from te.domain.clock import IST
from te.domain.costs import CostModel, select_rates
from te.domain.symbols import parse_option_symbol

_CHARGES = Path(__file__).resolve().parents[2] / "config" / "charges.yaml"
#: A Tuesday, with weekly expiries on the 11th (6 days out) and the 4th
#: (yesterday, so already gone) — the exact shape of the live account's
#: 5 August session.
ON = dt.date(2026, 8, 5)
ENTRY = dt.datetime(2026, 8, 5, 10, 30, tzinfo=IST)
STRIKE = 24_600


def _minute(i: int) -> dt.datetime:
    return dt.datetime(2026, 8, 5, 9, 15, tzinfo=IST) + dt.timedelta(minutes=i)


def _bars(symbol: str, price: float, n: int = 380) -> list[dict[str, object]]:
    return [
        {
            "symbol": symbol, "exchange": "NFO", "event_ts": _minute(i), "interval": "1m",
            "o": price, "h": price * 1.02, "l": price * 0.98, "c": price,
            "v": 1_000, "oi": 0, "ingested_at": _minute(i), "source": "test",
        }
        for i in range(n)
    ]


@pytest.fixture
def cost_model() -> CostModel:
    return CostModel(select_rates(load_charge_rate_table(_CHARGES), ON))


@pytest.fixture
def contracts(tmp_path: Path) -> tuple[BarStore, OptionContractIndex]:
    """Three expiries live at once, priced the way real ones are: the nearer
    the expiry the cheaper the option, since less time value remains."""
    store = BarStore(tmp_path / "bars")
    rows: list[dict[str, object]] = []
    for expiry_tag, price in (("06AUG26", 42.0), ("11AUG26", 138.0), ("18AUG26", 205.0)):
        rows += _bars(f"NIFTY{expiry_tag}{STRIKE}PE", price)
        rows += _bars(f"NIFTY{expiry_tag}{STRIKE}CE", price)
    store.append(pd.DataFrame(rows, columns=list(BAR_COLUMNS)))
    return store, OptionContractIndex(store, "NIFTY")


def _traded_expiry(
    contracts_fixture: tuple[BarStore, OptionContractIndex],
    cost_model: CostModel,
    *,
    lo: int,
    hi: int,
) -> dt.date | None:
    store, index = contracts_fixture
    trade, _unaffordable = _label(
        store=store,
        contracts=index,
        cost_model=cost_model,
        lot_size_for=lambda _day: 65,
        index_level=Decimal(STRIKE),
        entry_ts=ENTRY,
        direction="long_put",
        exchange="NFO",
        stop_pct=Decimal(20),
        target_pct=Decimal(20),
        max_hold=dt.timedelta(hours=3),
        strikes_out_of_the_money=0,
        min_days_to_expiry=lo,
        max_days_to_expiry=hi,
    )
    if trade is None:
        return None
    return parse_option_symbol(trade.option_symbol).expiry


def test_the_nearest_band_takes_the_nearest_expiry(contracts, cost_model) -> None:  # noqa: ANN001
    assert _traded_expiry(contracts, cost_model, lo=0, hi=1) == dt.date(2026, 8, 6)


def test_a_later_band_refuses_the_near_expiry(contracts, cost_model) -> None:  # noqa: ANN001
    """The load-bearing half. `nearest` enforces only the upper bound, so
    without the lower one a "5-7 days" band would quietly include the
    expiry-day contract and the bands would not be distinct samples."""
    assert _traded_expiry(contracts, cost_model, lo=5, hi=7) == dt.date(2026, 8, 11)


def test_a_band_the_archive_cannot_fill_takes_no_trade(contracts, cost_model) -> None:  # noqa: ANN001
    """No contract expires 3 or 4 days out here. Refusing is the honest
    answer — rolling to the 6-day one instead would report a result for a
    distance the data does not support."""
    assert _traded_expiry(contracts, cost_model, lo=3, hi=4) is None


def test_the_defaults_reproduce_the_old_pooled_behaviour(contracts, cost_model) -> None:  # noqa: ANN001
    """Every pre-existing caller and every recorded regression number must
    be untouched by this change."""
    assert _traded_expiry(contracts, cost_model, lo=0, hi=7) == dt.date(2026, 8, 6)


def test_the_bands_pick_genuinely_different_instruments(contracts, cost_model) -> None:  # noqa: ANN001
    """The whole reason the parameter exists: same signal, same day, same
    strike — different contract."""
    near = _traded_expiry(contracts, cost_model, lo=0, hi=1)
    far = _traded_expiry(contracts, cost_model, lo=5, hi=7)
    assert near is not None and far is not None
    assert near != far


def test_the_band_lower_edge_is_inclusive(contracts, cost_model) -> None:  # noqa: ANN001
    """`< min_days` vs `<= min_days` survived a mutation audit: no band in
    the original fixture had a lower edge equal to a listed days-to-expiry,
    so "INCLUSIVE" was documented and unchecked. The 11 Aug contract is
    exactly 6 days out."""
    assert _traded_expiry(contracts, cost_model, lo=6, hi=7) == dt.date(2026, 8, 11)
