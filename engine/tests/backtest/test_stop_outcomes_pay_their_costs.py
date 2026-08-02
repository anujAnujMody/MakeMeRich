"""A stopped-out trade must be scored INCLUDING the round trip it paid.

Found on 2026-08-02 by review. `_label` short-circuited the two barrier
outcomes with constants: `+target_pct/stop_pct` for a target and exactly
`-1.0` for a stop.

The target constant was correct. `premium_barrier_levels` deliberately adds
the cost to `target_level`, so a trade that reaches it nets the full target
distance and the constant matches.

The stop constant was not. `stop_level` is `entry - stop_distance` with no
cost adjustment — that is a statement about where the barrier SITS, not
about who pays the brokerage. A stopped-out trade still pays the full round
trip, so it loses `stop_distance + cost`. Recording it as exactly one stop
distance understated every losing trade by 3-6% of an R on the real cost
table, always in the direction that flatters the strategy.

Both outcomes are now priced the same way: take the level the position
exited at, subtract the real round trip. These tests pin the asymmetry so it
cannot come back as a "simplification".
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from pathlib import Path

import pandas as pd
import pytest

from te.backtest.strategy_lab import _label
from te.data.barstore import BarStore
from te.data.charges_loader import load_charge_rate_table
from te.domain.clock import IST
from te.domain.costs import CostModel
from te.domain.money import Paise
from te.domain.symbols import ParsedOptionSymbol
from te.ml.labeling import premium_barrier_levels

_DAY = dt.date(2026, 3, 10)
_ENTRY = dt.datetime.combine(_DAY, dt.time(10, 0), tzinfo=IST)
_LOT = 75
_STOP_PCT = Decimal(20)
_TARGET_PCT = Decimal(20)


@pytest.fixture
def rates() -> CostModel:
    return CostModel(load_charge_rate_table(Path(__file__).resolve().parents[2] / "config" / "charges.yaml"))


def test_a_stop_costs_more_than_one_r(rates: CostModel) -> None:
    """The arithmetic the bug denied. Exiting at `stop_level` loses the stop
    distance PLUS the round trip, so the R multiple is strictly worse than
    -1.0 — never exactly -1.0."""
    entry = Paise(100_00)
    stop_level, _target_level = premium_barrier_levels(
        entry_premium=entry, stop_pct=_STOP_PCT, target_pct=_TARGET_PCT,
        cost_model=rates, exchange="NFO", lot_size=_LOT, on=_DAY,
    )
    round_trip = rates.round_trip(
        entry_premium=entry, exit_premium=stop_level, qty=_LOT, exchange="NFO", on=_DAY
    ).total
    stop_distance = float(entry) * float(_STOP_PCT) / 100
    net = float(stop_level) - float(entry) - float(round_trip) / _LOT
    r = net / stop_distance

    assert r < -1.0, "a stop that pays its costs must be worse than -1.0 R"
    # Real magnitude, not a token check: the omitted cost was 3-6% of an R.
    assert -1.10 < r < -1.02, f"expected roughly -1.03..-1.06 R, got {r:.4f}"


def test_a_target_still_nets_the_full_target_distance(rates: CostModel) -> None:
    """The other half: `target_level` already contains the cost, so pricing
    it the same way must still come out at +ratio. A fix that made stops
    honest by making targets pessimistic would be just as wrong."""
    entry = Paise(100_00)
    _stop_level, target_level = premium_barrier_levels(
        entry_premium=entry, stop_pct=_STOP_PCT, target_pct=_TARGET_PCT,
        cost_model=rates, exchange="NFO", lot_size=_LOT, on=_DAY,
    )
    round_trip = rates.round_trip(
        entry_premium=entry, exit_premium=target_level, qty=_LOT, exchange="NFO", on=_DAY
    ).total
    stop_distance = float(entry) * float(_STOP_PCT) / 100
    net = float(target_level) - float(entry) - float(round_trip) / _LOT
    r = net / stop_distance

    expected = float(_TARGET_PCT / _STOP_PCT)
    # Within a paise of rounding on the cost estimate baked into the level.
    assert abs(r - expected) < 0.02, f"a target should net ~{expected}, got {r:.4f}"


def _store_with_option(tmp_path: Path, *, path: list[float]) -> tuple[BarStore, str]:
    """One option contract quoted every minute from the open."""
    symbol = "NIFTY10MAR26C24000"
    rows = []
    for minute, premium in enumerate(path):
        ts = dt.datetime.combine(_DAY, dt.time(9, 15), tzinfo=IST) + dt.timedelta(minutes=minute)
        rows.append(
            {
                "symbol": symbol, "exchange": "NFO", "event_ts": ts.astimezone(dt.UTC), "interval": "1m",
                "o": premium, "h": premium, "l": premium, "c": premium, "v": 0.0, "oi": 0,
                "ingested_at": ts.astimezone(dt.UTC), "source": "test",
            }
        )
    store = BarStore(tmp_path / "bars")
    store.append(pd.DataFrame(rows))
    return store, symbol


class _OneContract:
    """Resolves every request to the single contract the fixture wrote."""

    def __init__(self, symbol: str, strike: Decimal) -> None:
        self._contract = ParsedOptionSymbol(
            base="NIFTY", expiry=_DAY, strike=strike, option_type="CE", symbol=symbol
        )

    def nearest(self, **_kwargs: object) -> ParsedOptionSymbol:
        return self._contract


def test_label_scores_a_real_stopped_trade_below_minus_one(tmp_path: Path, rates: CostModel) -> None:
    """End to end through `_label`: a premium that falls straight through the
    stop must come back worse than -1.0 R, which is the number that reaches
    every reported mean R."""
    # Entry at 10:00 (minute 45) at 100, then a collapse well past the stop.
    path = [100.0] * 46 + [70.0] * (375 - 46)
    store, symbol = _store_with_option(tmp_path, path=path)

    trade = _label(
        store=store,
        contracts=_OneContract(symbol, Decimal(24000)),  # type: ignore[arg-type]
        cost_model=rates,
        lot_size_for=lambda _day: _LOT,
        index_level=Decimal(24000),
        entry_ts=_ENTRY,
        direction="long_call",
        exchange="NFO",
        stop_pct=_STOP_PCT,
        target_pct=_TARGET_PCT,
        max_hold=dt.timedelta(hours=3),
        strikes_out_of_the_money=0,
    )
    assert trade is not None
    assert trade.barrier == "stop"
    assert trade.r_multiple < -1.0, (
        f"a stopped trade scored {trade.r_multiple:.4f} — the round trip it paid is missing"
    )
