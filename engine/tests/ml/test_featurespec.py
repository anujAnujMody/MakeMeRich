"""`te.ml.featurespec` — the disjointness guarantee. A `FeatureSpec` must be
structurally incapable of leaking ORB's own vocabulary into the secondary
model's feature columns."""

from __future__ import annotations

import pytest

from te.ml.featurespec import PRIMARY_VOCABULARY, SECONDARY_V1, FeatureSpec


def test_secondary_spec_encodes_day_of_week_cyclically() -> None:
    """`day_of_week` was a raw ordinal 0-6, which asserts a false ordering
    and a false distance: Friday (4) and Monday (0) are adjacent trading
    days but maximally far apart on that scale. Two columns, sin and cos,
    make that adjacency real."""
    assert SECONDARY_V1.columns == (
        "iv_rank_60d",
        "india_vix_level",
        "india_vix_term_slope",
        "rv_iv_spread",
        "day_of_week_sin",
        "day_of_week_cos",
        "minutes_from_open",
        "dte",
    )
    assert "day_of_week" not in SECONDARY_V1.columns
    assert SECONDARY_V1.name == "secondary"
    # Bumped from 1 — the feature-vector SHAPE changed (7 columns -> 8), so a
    # model trained against the old spec is not loadable against this one.
    assert SECONDARY_V1.version == 2


def test_secondary_v1_columns_are_disjoint_from_primary_vocabulary() -> None:
    assert not (set(SECONDARY_V1.columns) & PRIMARY_VOCABULARY)


@pytest.mark.parametrize("leaked_column", sorted(PRIMARY_VOCABULARY))
def test_constructing_a_spec_with_any_orb_vocabulary_column_raises(leaked_column: str) -> None:
    with pytest.raises(ValueError, match="leaks primary features"):
        FeatureSpec(name="bad", version=1, columns=("dte", leaked_column))


def test_constructing_a_spec_with_no_overlap_succeeds() -> None:
    spec = FeatureSpec(name="ok", version=1, columns=("dte", "day_of_week_sin"))
    assert spec.columns == ("dte", "day_of_week_sin")


def test_primary_vocabulary_mirrors_orb_condition_vocabulary() -> None:
    """The plan: "range high/low, range width, breakout close, breakout
    volume vs range-average volume, direction" — verify the exact set."""
    assert PRIMARY_VOCABULARY == frozenset(
        {
            "range_high",
            "range_low",
            "range_width",
            "breakout_close",
            "breakout_volume",
            "range_avg_volume",
            "direction",
        }
    )
