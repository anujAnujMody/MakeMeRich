"""Picking a strike by ARCHIVE POSITION is not the same as picking it by
DISTANCE, and our chains have holes.

Measured on the real archive 2026-08-05: NIFTY's 2024-01-04 expiry lists 85
gaps of 50 points but also gaps of 100, 150 and 200; BANKNIFTY's 2026-03-30
expiry lists 157 gaps of 100 alongside one of 700 and two of 1,000. So
`strikes_out_of_the_money=3` is 150 points on a complete chain and can be
1,200 points on a holed one — two materially different options that every
sweep so far has pooled into one average.

`otm_points` names the distance directly and REFUSES when the archive holds
no strike near it. These tests build chains with holes on purpose, because
a chain without one cannot tell the two selection modes apart.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from pathlib import Path

import pandas as pd
import pytest

from te.data.barstore import BAR_COLUMNS, BarStore
from te.data.option_history import OptionContractIndex
from te.domain.clock import IST

EXPIRY = dt.date(2026, 8, 27)
ON = dt.date(2026, 8, 25)


def _index(tmp_path: Path, strikes: list[int], *, base: str = "BANKNIFTY") -> OptionContractIndex:
    """A real `BarStore` — the index is built by scanning `symbol=`
    partitions, so a fake in-memory listing would not exercise it."""
    store = BarStore(tmp_path / "bars")
    ts = dt.datetime(2026, 8, 25, 9, 20, tzinfo=IST)
    rows = []
    for strike in strikes:
        for option_type in ("CE", "PE"):
            rows.append(
                {
                    "symbol": f"{base}27AUG26{strike}{option_type}",
                    "exchange": "NFO",
                    "event_ts": ts,
                    "interval": "1m",
                    "o": 100.0, "h": 101.0, "l": 99.0, "c": 100.0,
                    "v": 1_000, "oi": 0, "ingested_at": ts, "source": "test",
                }
            )
    store.append(pd.DataFrame(rows, columns=list(BAR_COLUMNS)))
    return OptionContractIndex(store, base)


#: 56000..56400 complete at 100, then a 700-point hole, then 57100+. Shaped
#: after BANKNIFTY's real 2026-03-30 chain.
HOLED = [56000, 56100, 56200, 56300, 56400, 57100, 57200, 57300]
SPOT = Decimal(56_000)


def test_strike_step_reports_the_listed_interval_not_the_holes(tmp_path: Path) -> None:
    index = _index(tmp_path, HOLED)
    assert index.strike_step(expiry=EXPIRY, option_type="CE") == Decimal(100)


def test_counting_positions_lands_far_from_the_distance_it_implies(tmp_path: Path) -> None:
    """The defect itself, asserted rather than described: six positions out
    on this chain is 1,200 points, not the 600 the label implies."""
    index = _index(tmp_path, HOLED)
    picked = index.nearest(
        on=ON, index_level=SPOT, option_type="CE", strikes_out_of_the_money=6, max_days_to_expiry=7
    )
    assert picked is not None
    assert picked.strike == Decimal(57_200)
    assert picked.strike - SPOT == Decimal(1_200)


def test_points_selection_lands_on_the_strike_that_distance_names(tmp_path: Path) -> None:
    index = _index(tmp_path, HOLED)
    picked = index.nearest(
        on=ON, index_level=SPOT, option_type="CE", otm_points=Decimal(300), max_days_to_expiry=7
    )
    assert picked is not None
    assert picked.strike == Decimal(56_300)


def test_a_distance_inside_the_hole_is_refused_not_approximated(tmp_path: Path) -> None:
    """600 points out is 56,600 — a strike this archive does not hold. The
    nearest listed strike is 400 points away, so returning it would report a
    result for a distance the data cannot support."""
    index = _index(tmp_path, HOLED)
    assert (
        index.nearest(on=ON, index_level=SPOT, option_type="CE", otm_points=Decimal(600), max_days_to_expiry=7)
        is None
    )


def test_a_distance_off_grid_by_less_than_half_a_step_still_resolves(tmp_path: Path) -> None:
    """Spot is almost never exactly on a strike. 240 points from 56,000 is
    56,240, which rounds to the listed 56,200 — 40 points off, well inside
    half a 100-point step, so it resolves rather than refusing."""
    index = _index(tmp_path, HOLED)
    picked = index.nearest(
        on=ON, index_level=SPOT, option_type="CE", otm_points=Decimal(240), max_days_to_expiry=7
    )
    assert picked is not None
    assert picked.strike == Decimal(56_200)


def test_puts_step_down_from_spot(tmp_path: Path) -> None:
    """Out-of-the-money is the opposite direction for a put — a sign error
    here would silently buy in-the-money options at several times the
    premium."""
    index = _index(tmp_path, HOLED)
    picked = index.nearest(
        on=ON, index_level=Decimal(56_300), option_type="PE", otm_points=Decimal(200), max_days_to_expiry=7
    )
    assert picked is not None
    assert picked.strike == Decimal(56_100)


def test_the_two_distance_arguments_cannot_be_combined(tmp_path: Path) -> None:
    """They are two names for one thing; accepting both would silently
    honour one and drop the other."""
    index = _index(tmp_path, HOLED)
    with pytest.raises(ValueError, match="not both"):
        index.nearest(
            on=ON, index_level=SPOT, option_type="CE",
            strikes_out_of_the_money=2, otm_points=Decimal(200), max_days_to_expiry=7,
        )


def test_position_counting_is_unchanged_on_a_complete_chain(tmp_path: Path) -> None:
    """The existing callers must keep behaving exactly as before — on a
    chain with no holes the two modes agree, which is why the defect stayed
    invisible."""
    complete = [56_000 + 100 * i for i in range(9)]
    index = _index(tmp_path, complete)
    by_position = index.nearest(
        on=ON, index_level=SPOT, option_type="CE", strikes_out_of_the_money=3, max_days_to_expiry=7
    )
    by_points = index.nearest(
        on=ON, index_level=SPOT, option_type="CE", otm_points=Decimal(300), max_days_to_expiry=7
    )
    assert by_position is not None and by_points is not None
    assert by_position.symbol == by_points.symbol


def test_strike_step_reports_the_MODE_not_the_minimum(tmp_path: Path) -> None:  # noqa: N802
    """A mutation audit found `mode -> min` survived the whole suite: the
    `HOLED` chain above has one gap size plus wide holes, so mode, minimum
    and every tie-break rule agree on it. This chain does not — it carries a
    single 50-point gap among 100s, where the minimum is 50 and the listed
    interval is 100."""
    index = _index(tmp_path, [56_000, 56_050, 56_150, 56_250, 56_350, 56_450])
    assert index.strike_step(expiry=EXPIRY, option_type="CE") == Decimal(100)


def test_strike_step_breaks_a_tie_toward_the_finer_gap(tmp_path: Path) -> None:
    """Two 50s and two 100s. The docstring says the finer width is the
    listed interval and the wider one is that interval with a hole in it, so
    the tie must resolve to 50. `min -> max` among modal gaps also survived
    the audit."""
    index = _index(tmp_path, [56_000, 56_050, 56_100, 56_200, 56_300])
    assert index.strike_step(expiry=EXPIRY, option_type="CE") == Decimal(50)


def test_a_distance_exactly_half_a_step_off_grid_is_refused(tmp_path: Path) -> None:
    """The boundary itself, never exercised before: on a 100-point chain, 50
    points off grid sits exactly `step / 2` away from the nearest listed
    strike (56,000 or 56,100 are equidistant; the tie-break picks the
    lower). `>=` (not `>`) must refuse it — the comment above the check in
    `option_history.py` explains why the tie-break matters: resolving it
    would narrow a call wing and widen a put wing by half a step from the
    same request."""
    index = _index(tmp_path, HOLED)
    assert (
        index.nearest(on=ON, index_level=SPOT, option_type="CE", otm_points=Decimal(50), max_days_to_expiry=7)
        is None
    )


def test_a_single_strike_chain_has_no_measurable_step(tmp_path: Path) -> None:
    """And `nearest` must then REFUSE rather than return the only strike
    listed however far it sits from the request — `if step is None: return
    None` survived the audit untested."""
    index = _index(tmp_path, [56_000])
    assert index.strike_step(expiry=EXPIRY, option_type="CE") is None
    assert (
        index.nearest(on=ON, index_level=SPOT, option_type="CE", otm_points=Decimal(500), max_days_to_expiry=7)
        is None
    )
