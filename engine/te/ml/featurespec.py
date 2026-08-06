"""`FeatureSpec` and `PRIMARY_VOCABULARY` — the disjointness guarantee that
makes meta-labeling work rather than "squeeze the same orange twice" (the
plan's own framing of the QuantConnect critique this design sidesteps).

`PRIMARY_VOCABULARY` is `te/strategy/orb.py`'s exact vocabulary, re-derived
from its condition/feature names (opening range high/low, range width,
breakout close, breakout volume vs range-average volume, direction) — see
that module's docstring, which itself points back here. If ORB's vocabulary
ever grows, this set (and the disjointness test) must grow with it by hand;
nothing here can infer it automatically.

`FeatureSpec.__post_init__` raises the moment a spec is CONSTRUCTED with a
column that overlaps `PRIMARY_VOCABULARY` — checked again at call time inside
`te.ml.dataset.build_training_set`, per the plan ("checked at import time and
again inside build_training_set"), so a spec built once and reused later
can't drift.
"""

from __future__ import annotations

from dataclasses import dataclass

#: `te/strategy/orb.py`'s vocabulary — see that module's docstring. A
#: secondary `FeatureSpec` may never use any of these names as a column,
#: structurally enforced by `FeatureSpec.__post_init__`.
PRIMARY_VOCABULARY: frozenset[str] = frozenset(
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


@dataclass(frozen=True)
class FeatureSpec:
    """A named, versioned set of feature column names for `te.ml`. Raises
    `ValueError` at construction if any column intersects
    `PRIMARY_VOCABULARY` — this is what makes it structurally impossible for
    a secondary model to see the primary rule's own vocabulary."""

    name: str
    version: int
    columns: tuple[str, ...]

    def __post_init__(self) -> None:
        overlap = set(self.columns) & PRIMARY_VOCABULARY
        if overlap:
            raise ValueError(f"secondary spec leaks primary features: {sorted(overlap)}")


#: The secondary spec — "given the ORB fired here, will it hit its net target
#: before its net stop?"
#:
#: **version 2** (was 1): `day_of_week` was a raw ordinal 0-6, which encodes a
#: false ordering and a false distance — Friday (4) and Monday (0) are
#: adjacent trading days but sit maximally far apart on that scale, and
#: "Wednesday > Tuesday" is meaningless. It is now two columns,
#: `day_of_week_sin`/`day_of_week_cos` (a point on a 7-period circle), which
#: makes the wrap-around adjacency real. That changed the feature vector's
#: SHAPE from 7 columns to 8, so the version is bumped: a model trained
#: against version 1 cannot be served against this spec.
#: Version 3 drops `india_vix_term_slope`. The term structure needs a
#: near-term and a next-term India VIX series, and neither exists: a broker
#: instrument search on 2026-07-31 returned exactly ONE India VIX symbol —
#: `INDIAVIX`, the spot index (`instrumenttype: AMXIDX`). NSE does not list
#: tradeable VIX futures, so this is not a backfill gap that more effort
#: would close; the data does not exist at this source.
#:
#: Keeping a permanently-NaN column would be worse than dropping it. It
#: consumes a slot against `validate_training_set`'s features-per-sample
#: overfitting limit, and it lets the model be described as using eight
#: features when it can only ever use seven. `build_training_set` would
#: have returned NaN for it on every single row, and nothing would have
#: raised — the same silent-degradation shape as the missing daily bars.
#:
#: Restore it (and bump the version again) only if a real near/next VIX
#: series becomes available.
SECONDARY_V1 = FeatureSpec(
    name="secondary",
    version=3,
    columns=(
        "iv_rank_60d",
        "india_vix_level",
        "rv_iv_spread",
        "day_of_week_sin",
        "day_of_week_cos",
        "minutes_from_open",
        "dte",
    ),
)
