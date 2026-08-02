"""When one bar touches BOTH barriers, the label must be a loss.

A 1-minute bar records a high and a low but not the order they happened in.
So when the stop and the target both sit inside a single bar's range, the
true outcome is genuinely unknowable from the data we hold.

Until 2026-08-01 both barrier walks checked the TARGET first, which silently
resolved every one of those ambiguous bars as a win. That is a systematic
optimistic bias on exactly the most volatile bars — the ones most likely to
straddle both barriers — and nothing downstream could reveal it: the labels
are well-formed, the win rate is plausible, and the bias is invisible in
every summary statistic.

Resolving ties as losses is the conservative reading, and it matches live
behaviour: a stop is a resting order that fills the moment price touches it,
while a target needs the move to actually get there and hold. NautilusTrader
treats the same ambiguity as an explicit configurable rather than quietly
choosing the favourable side.

These tests pin the tie-break on BOTH walks — the index path (which must
also invert for puts) and the real-premium path (which never inverts,
because buying a put is still being long that put's premium).
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pandas as pd
import pytest

from te.data.barstore import BarStore
from te.domain.costs import CostModel
from te.domain.money import Paise
from te.ml.labeling import label_one_firing, label_one_firing_on_premium

_ENTRY = dt.datetime(2026, 3, 10, 5, 0, tzinfo=dt.UTC)


@pytest.fixture
def rates():  # noqa: ANN201
    from pathlib import Path

    from te.data.charges_loader import load_charge_rate_table

    table = load_charge_rate_table(Path(__file__).resolve().parents[2] / "config" / "charges.yaml")
    return CostModel(table)


def _store(tmp_path, symbol: str, bars: list[tuple[dt.datetime, float, float, float, float]]) -> BarStore:  # noqa: ANN001
    """A `BarStore` holding exactly the bars given, as (ts, o, h, l, c)."""
    store = BarStore(tmp_path / "bars")
    store.append(
        pd.DataFrame(
            [
                {
                    "symbol": symbol,
                    "exchange": "NFO",
                    "event_ts": pd.Timestamp(ts),
                    "interval": "1m",
                    "o": o,
                    "h": h,
                    "l": low,
                    "c": c,
                    "v": 0.0,
                    "oi": 0,
                    "ingested_at": pd.Timestamp(ts),
                    "source": "test",
                }
                for ts, o, h, low, c in bars
            ]
        )
    )
    return store


def test_premium_path_resolves_a_straddling_bar_as_a_stop(tmp_path, rates) -> None:  # noqa: ANN001
    """Entry at 100.00. The next bar's range spans BOTH a -20% stop (80) and
    a +20% target (120). The honest answer is 'unknown'; the safe one is
    'stop'."""
    store = _store(
        tmp_path,
        "NIFTY10MAR26C24500",
        [
            (_ENTRY - dt.timedelta(minutes=1), 100.0, 100.0, 100.0, 100.0),
            # One violent bar covering both barriers.
            (_ENTRY + dt.timedelta(minutes=1), 100.0, 130.0, 70.0, 100.0),
        ],
    )
    label = label_one_firing_on_premium(
        store=store,
        option_symbol="NIFTY10MAR26C24500",
        entry_ts=_ENTRY,
        stop_pct=Decimal(20),
        target_pct=Decimal(20),
        max_hold=dt.timedelta(hours=1),
        cost_model=rates,
        exchange="NFO",
        lot_size=65,
    )
    assert label is not None
    assert label.barrier == "stop", "a bar touching both barriers must not be scored as a win"
    assert label.label == 0


def test_premium_path_still_reports_a_clean_target(tmp_path, rates) -> None:  # noqa: ANN001
    """The tie-break must not turn genuine winners into losses — a bar that
    reaches the target WITHOUT reaching the stop is still a win."""
    store = _store(
        tmp_path,
        "NIFTY10MAR26C24500",
        [
            (_ENTRY - dt.timedelta(minutes=1), 100.0, 100.0, 100.0, 100.0),
            (_ENTRY + dt.timedelta(minutes=1), 100.0, 130.0, 99.0, 128.0),
        ],
    )
    label = label_one_firing_on_premium(
        store=store,
        option_symbol="NIFTY10MAR26C24500",
        entry_ts=_ENTRY,
        stop_pct=Decimal(20),
        target_pct=Decimal(20),
        max_hold=dt.timedelta(hours=1),
        cost_model=rates,
        exchange="NFO",
        lot_size=65,
    )
    assert label is not None
    assert label.barrier == "target"
    assert label.label == 1


@pytest.mark.parametrize("direction", ["long_call", "long_put"])
def test_index_path_resolves_a_straddling_bar_as_a_stop(tmp_path, rates, direction: str) -> None:  # noqa: ANN001
    """The index walk inverts for puts, so the tie-break has to be correct in
    both orientations — a put firing whose bar straddles both levels must
    also be a stop, not a win."""
    store = _store(
        tmp_path,
        "NIFTY",
        [
            (_ENTRY - dt.timedelta(minutes=1), 24_000.0, 24_000.0, 24_000.0, 24_000.0),
            (_ENTRY + dt.timedelta(minutes=1), 24_000.0, 24_400.0, 23_600.0, 24_000.0),
        ],
    )
    label, barrier, _ = label_one_firing(
        store=store,
        instrument="NIFTY",
        direction=direction,
        entry_ts=_ENTRY,
        entry_premium=Paise(2_400_000),
        stop_distance=Paise(20_000),
        target_distance=Paise(20_000),
        max_hold=dt.timedelta(hours=1),
        cost_model=rates,
        exchange="NFO",
    )
    assert barrier == "stop", f"{direction}: a straddling bar must not be scored as a win"
    assert label == 0
