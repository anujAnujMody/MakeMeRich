"""Properties, not examples — the tests the example-based suite could not be.

A mutation audit on 2026-08-05 ran 33 real mutations against the five
example-based test files in this change. **23 survived.** Deleting six of
the eight cost legs survived. Charging every leg as a BUY survived. Making
the take-profit fire on the first minute after entry survived. Deleting the
strangle guard survived.

The cause is structural, not carelessness: those tests assert the value the
implementation happens to produce on a fixture built to be easy to reason
about, and they were written by the author of the code from the same mental
model. When the model is wrong — which it was twice in one day — the tests
agree with the code and both are wrong together.

These tests assert PROPERTIES that must hold whatever the implementation
does, over generated paths rather than one hand-picked one. A property does
not care what the author believed.
"""

from __future__ import annotations

import datetime as dt
import random
from decimal import Decimal
from pathlib import Path

import pandas as pd
import pytest

from te.backtest.straddle_lab import (
    StraddleGeometry,
    resolve_legs,
    score_trade,
    walk,
)
from te.data.barstore import BAR_COLUMNS, BarStore
from te.data.charges_loader import load_charge_rate_table
from te.data.option_history import OptionContractIndex
from te.domain.clock import IST
from te.domain.costs import CostModel, select_rates

_CHARGES = Path(__file__).resolve().parents[2] / "config" / "charges.yaml"
DAY = dt.date(2026, 8, 25)
ENTRY = dt.datetime(2026, 8, 25, 9, 20, tzinfo=IST)
STRIKE = 25_000
WING = 100
LOT = 75
MINUTES = 120


@pytest.fixture
def cost_model() -> CostModel:
    return CostModel(select_rates(load_charge_rate_table(_CHARGES), DAY))


def _series(symbol: str, closes: list[float]) -> list[dict[str, object]]:
    base = dt.datetime(2026, 8, 25, 9, 15, tzinfo=IST)
    return [
        {
            "symbol": symbol, "exchange": "NFO", "event_ts": base + dt.timedelta(minutes=i),
            "interval": "1m", "o": c, "h": c, "l": c, "c": c, "v": 1_000, "oi": 0,
            "ingested_at": base + dt.timedelta(minutes=i), "source": "test",
        }
        for i, c in enumerate(closes)
    ]


def _arbitrage_consistent_paths(rng: random.Random) -> dict[str, list[float]]:
    """A random but PRICE-COHERENT four-leg path.

    The constraints are the ones real option prices obey, and they are what
    make the bound testable at all — a path where only a short leg moves is
    not a market, and would "prove" a bound violation that pricing forbids:

    * every premium is non-negative
    * a wing is never worth more than the short it protects (it is further
      out of the money on the same expiry)
    * neither vertical is worth more than its width (`short - wing <= WING`)

    Within those, the index is allowed to wander anywhere, including through
    both wings.
    """
    call, put, call_wing, put_wing = [], [], [], []
    spot = 0.0
    for _ in range(MINUTES):
        spot += rng.uniform(-25, 25)
        # Intrinsic plus a decaying time value, floored at zero.
        time_value = max(0.0, rng.uniform(10, 60))
        c = max(0.0, spot) + time_value
        p = max(0.0, -spot) + time_value
        # A wing is the same option WING points further out: its intrinsic
        # starts WING later, and it carries less time value.
        cw = max(0.0, spot - WING) + time_value * rng.uniform(0.3, 0.95)
        pw = max(0.0, -spot - WING) + time_value * rng.uniform(0.3, 0.95)
        # Clamp each vertical to its width — the no-arbitrage bound.
        cw = max(cw, c - WING)
        pw = max(pw, p - WING)
        call.append(round(c, 2))
        put.append(round(p, 2))
        call_wing.append(round(min(cw, c), 2))
        put_wing.append(round(min(pw, p), 2))
    return {"call": call, "put": put, "call_wing": call_wing, "put_wing": put_wing}


def _store_for(tmp_path: Path, paths: dict[str, list[float]], seed: int) -> OptionContractIndex:
    store = BarStore(tmp_path / f"bars-{seed}")
    rows: list[dict[str, object]] = []
    rows += _series("NIFTY", [float(STRIKE)] * MINUTES)
    rows += _series(f"NIFTY27AUG26{STRIKE}CE", paths["call"])
    rows += _series(f"NIFTY27AUG26{STRIKE}PE", paths["put"])
    rows += _series(f"NIFTY27AUG26{STRIKE + WING}CE", paths["call_wing"])
    rows += _series(f"NIFTY27AUG26{STRIKE - WING}PE", paths["put_wing"])
    for offset in (-200, -150, -50, 50, 150, 200):
        rows += _series(f"NIFTY27AUG26{STRIKE + offset}CE", [30.0] * MINUTES)
        rows += _series(f"NIFTY27AUG26{STRIKE + offset}PE", [30.0] * MINUTES)
    store.append(pd.DataFrame(rows, columns=list(BAR_COLUMNS)))
    return OptionContractIndex(store, "NIFTY"), store


def _geometry(**overrides: object) -> StraddleGeometry:
    base: dict[str, object] = {
        "entry_time": dt.time(9, 20),
        "wing_points": Decimal(WING),
        "leg_stop_fraction": None,
        "take_profit_fraction": None,
        "min_days_to_expiry": 0,
        "max_days_to_expiry": 7,
    }
    return StraddleGeometry(**{**base, **overrides})  # type: ignore[arg-type]


@pytest.mark.parametrize("seed", range(40))
@pytest.mark.parametrize(
    "geometry_kwargs",
    [
        {},
        {"leg_stop_fraction": Decimal("0.25")},
        {"take_profit_fraction": Decimal("0.5")},
        {"leg_stop_fraction": Decimal("0.3"), "take_profit_fraction": Decimal("0.5")},
    ],
    ids=["plain", "stop", "target", "stop+target"],
)
def test_gross_loss_never_exceeds_the_risk_unit(
    tmp_path: Path, cost_model: CostModel, seed: int, geometry_kwargs: dict[str, object]
) -> None:
    """THE property. `max_loss_per_unit` is what position sizing divides by,
    so a trade that loses more than it means the account was sized off a
    number smaller than the truth — which is exactly the defect that shipped
    this morning (`wing - credit`, the EXPIRY bound, on a strategy that
    never reaches expiry).

    Compared GROSS, because `max_loss_per_unit` is a gross bound: charges
    are real money the structure loses on top of it, counted separately by
    `label_positions`. Including them here would fail for a correct
    implementation.

    The last parametrisation is the stop+target interaction, which the
    example suite never covers at all — no test there sets both.
    """
    rng = random.Random(seed)
    paths = _arbitrage_consistent_paths(rng)
    contracts, store = _store_for(tmp_path, paths, seed)
    geometry = _geometry(**geometry_kwargs)

    legs = resolve_legs(contracts, on=DAY, index_level=Decimal(STRIKE), geometry=geometry)
    if isinstance(legs, str):
        pytest.skip(f"path did not resolve: {legs}")
    walked = walk(store=store, legs=legs, entry_ts=ENTRY, geometry=geometry)
    if isinstance(walked, str):
        pytest.skip(f"path did not walk: {walked}")

    credit, _net, max_loss, _r = score_trade(
        walked=walked, legs=legs, lot_size=LOT, exchange="NFO", cost_model=cost_model, on=DAY
    )
    gross_per_unit = (
        (int(walked.call_entry) - int(walked.call_exit))
        + (int(walked.put_entry) - int(walked.put_exit))
        + (int(walked.call_wing_exit) - int(walked.call_wing_entry))
        + (int(walked.put_wing_exit) - int(walked.put_wing_entry))
    )
    assert int(max_loss) > 0, "a risk unit of zero would size an unbounded number of lots"
    assert -gross_per_unit <= int(max_loss), (
        f"seed {seed} {geometry_kwargs}: lost {-gross_per_unit}p against a risk unit of {int(max_loss)}p "
        f"(credit {int(credit)}p)"
    )


@pytest.mark.parametrize("seed", range(20))
def test_the_expiry_bound_is_genuinely_insufficient(tmp_path: Path, cost_model: CostModel, seed: int) -> None:
    """The other half, and the one that makes the `2 x wing` load-bearing
    rather than decorative: there must EXIST arbitrage-consistent paths that
    break the expiry bound. Without this, `1 x wing` would pass the property
    above on every path and the multiplier would be untested.

    Collected across seeds rather than asserted per-seed — most paths do not
    reach a full-width loss, and requiring every one to would be asserting
    something false.
    """
    rng = random.Random(1000 + seed)
    paths = _arbitrage_consistent_paths(rng)
    contracts, store = _store_for(tmp_path, paths, 1000 + seed)
    geometry = _geometry()
    legs = resolve_legs(contracts, on=DAY, index_level=Decimal(STRIKE), geometry=geometry)
    if isinstance(legs, str):
        pytest.skip(f"path did not resolve: {legs}")
    walked = walk(store=store, legs=legs, entry_ts=ENTRY, geometry=geometry)
    if isinstance(walked, str):
        pytest.skip(f"path did not walk: {walked}")
    credit, _net, max_loss, _r = score_trade(
        walked=walked, legs=legs, lot_size=LOT, exchange="NFO", cost_model=cost_model, on=DAY
    )
    expiry_bound = WING * 100 - int(credit)
    gross_per_unit = (
        (int(walked.call_entry) - int(walked.call_exit))
        + (int(walked.put_entry) - int(walked.put_exit))
        + (int(walked.call_wing_exit) - int(walked.call_wing_entry))
        + (int(walked.put_wing_exit) - int(walked.put_wing_entry))
    )
    _breaks_expiry_bound.append(-gross_per_unit > expiry_bound)
    assert -gross_per_unit <= int(max_loss)


#: Filled by the test above; asserted once at the end of the module's run.
_breaks_expiry_bound: list[bool] = []


def test_at_least_one_generated_path_breaks_the_expiry_bound() -> None:
    """Runs last (alphabetically after the parametrised test above, which
    pytest executes in file order). If NO path exceeded `wing - credit`,
    the generator is too tame to distinguish the two bounds and this suite
    is not testing what it claims to."""
    assert _breaks_expiry_bound, "the generating test did not run"
    assert any(_breaks_expiry_bound), (
        "no generated path exceeded the expiry bound, so these paths cannot tell "
        "`wing - credit` from `2 x wing - credit` — widen the generator"
    )
