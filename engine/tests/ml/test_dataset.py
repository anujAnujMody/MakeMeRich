"""`te.ml.dataset.build_training_set` — the ONE path from bars to features.
Every bar read goes through `bars_asof`; training and live inference must be
provably identical for the same `as_of`."""

from __future__ import annotations

import datetime as dt
import math
from decimal import Decimal
from pathlib import Path

import pandas as pd
import pytest

from te.data.barstore import BAR_COLUMNS, BarStore
from te.domain.clock import IST
from te.domain.symbols import build_option_symbol
from te.ml.dataset import build_training_set
from te.ml.featurespec import SECONDARY_V1, FeatureSpec

INSTRUMENT = "NIFTY30JUN2626500CE"
UNDERLYING = "NIFTY"  # `parse_option_symbol(INSTRUMENT).base`
VIX_SYMBOL = "INDIAVIX"


def _bar(symbol: str, event_ts: dt.datetime, *, c: float, interval: str = "1d") -> dict[str, object]:
    return {
        "symbol": symbol,
        "exchange": "NFO",
        "event_ts": event_ts,
        "interval": interval,
        "o": c,
        "h": c,
        "l": c,
        "c": c,
        "v": 1000,
        "oi": 0,
        "ingested_at": event_ts,
        "source": "test",
    }


def _day(n: int) -> dt.datetime:
    return dt.datetime(2026, 6, 1, tzinfo=IST) + dt.timedelta(days=n)


@pytest.fixture
def store(tmp_path: Path) -> BarStore:
    s = BarStore(tmp_path / "bars")
    rows = []
    # 70 days of VIX daily closes, oscillating so iv_rank isn't degenerate.
    for i in range(70):
        level = 12.0 + (i % 5)
        rows.append(_bar(VIX_SYMBOL, _day(i), c=level))
    # 70 days of the UNDERLYING INDEX's own daily closes — this is what
    # `rv_iv_spread`'s realized-vol leg reads. Recorded for every traded
    # underlying by `te.engine.scheduler`'s WS subscription list.
    for i in range(70):
        rows.append(_bar(UNDERLYING, _day(i), c=24_000.0 + 30.0 * (i % 7)))
    # 25 days of the option instrument's own daily closes. Deliberately
    # SHORTER than the underlying's history — a weekly-expiry contract has
    # only existed for a few days, which is exactly why the realized-vol leg
    # must not be computed from it.
    for i in range(45, 70):
        rows.append(_bar(INSTRUMENT, _day(i), c=30.0 + 0.1 * i))
    s.append(pd.DataFrame(rows, columns=list(BAR_COLUMNS)))
    return s


def test_build_training_set_returns_exactly_the_spec_columns(store: BarStore) -> None:
    as_of = _day(69) + dt.timedelta(hours=6)  # well after the last daily bar closes
    result = build_training_set(as_of, SECONDARY_V1, store, None, instrument=INSTRUMENT)
    assert tuple(result.index) == SECONDARY_V1.columns


def test_build_training_set_reasserts_disjointness_at_call_time(store: BarStore) -> None:
    leaking_spec = object.__new__(FeatureSpec)
    object.__setattr__(leaking_spec, "name", "leaky")
    object.__setattr__(leaking_spec, "version", 1)
    object.__setattr__(leaking_spec, "columns", ("dte", "range_high"))
    as_of = _day(69) + dt.timedelta(hours=6)
    with pytest.raises(ValueError, match="leaks primary features"):
        build_training_set(as_of, leaking_spec, store, None, instrument=INSTRUMENT)


def test_inference_features_match_training_features_for_same_timestamp(store: BarStore) -> None:
    """The online/offline-skew regression test: training-mode and
    inference-mode calls at the SAME as_of must be element-wise identical,
    because both literally call the same function."""
    as_of = _day(69) + dt.timedelta(hours=6)

    training_row = build_training_set(as_of, SECONDARY_V1, store, None, instrument=INSTRUMENT, for_inference=False)
    inference_row = build_training_set(as_of, SECONDARY_V1, store, None, instrument=INSTRUMENT, for_inference=True)

    pd.testing.assert_series_equal(training_row, inference_row, check_names=False)


def test_the_spec_does_not_ask_for_a_vix_term_slope_that_cannot_exist(store: BarStore) -> None:
    """India VIX has no near/next-term series to build a term structure from
    — a broker instrument search on 2026-07-31 returned exactly one India
    VIX symbol, the spot index. The column was therefore NaN on every row
    forever, which `validate_training_set` skips rather than rejects: the
    model would silently have trained on 7 of its 8 declared features.

    Dropped from the spec rather than left in place returning NaN."""
    as_of = _day(69) + dt.timedelta(hours=6)
    result = build_training_set(as_of, SECONDARY_V1, store, None, instrument=INSTRUMENT)

    assert "india_vix_term_slope" not in SECONDARY_V1.columns
    assert "india_vix_term_slope" not in result.index


def test_dte_computed_from_option_symbol_expiry(store: BarStore) -> None:
    as_of = dt.datetime(2026, 6, 20, 10, 0, tzinfo=IST)
    result = build_training_set(as_of, SECONDARY_V1, store, None, instrument=INSTRUMENT)
    assert result["dte"] == 10.0  # expiry 30-Jun-2026, as_of 20-Jun-2026


def test_dte_on_an_index_instrument_uses_the_archives_real_historical_expiry_not_todays_weekday(
    store: BarStore,
) -> None:
    """The bug: `_dte`'s fallback (for an INDEX instrument, which carries no
    expiry of its own) used to resolve every `as_of` against TODAY's expiry
    weekday table (`te.domain.symbols._WEEKLY_EXPIRY_WEEKDAY`, NIFTY ->
    Tuesday), even for a firing from BEFORE that regime existed. NIFTY's real
    weekly expiry on 2024-01-04 was a THURSDAY (the pre Nov-2024/Sep-2025
    regime) — today's table wrongly resolves `as_of=2024-01-02` (a Tuesday)
    to dte=0 (that same Tuesday), when the real contract traded until
    2024-01-04, two days later.

    The archive has a real NIFTY option recorded with that historical
    2024-01-04 (Thursday) expiry — `_dte` must resolve `dte` from THAT, not
    from today's calendar.
    """
    historical_symbol = build_option_symbol("NIFTY", dt.date(2024, 1, 4), Decimal(21500), "CE")
    rows = [_bar(historical_symbol, dt.datetime(2024, 1, 2, 10, 0, tzinfo=IST), c=150.0, interval="1m")]
    store.append(pd.DataFrame(rows, columns=list(BAR_COLUMNS)))

    as_of = dt.datetime(2024, 1, 2, 10, 0, tzinfo=IST)  # a Tuesday
    result = build_training_set(as_of, SECONDARY_V1, store, None, instrument="NIFTY")

    assert result["dte"] == 2.0, (
        f"got {result['dte']}: resolved against today's Tuesday expiry table instead of the archive's "
        "real 2024-01-04 (Thursday) contract"
    )


def test_india_vix_level_is_computed_once_per_feature_row(
    store: BarStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`india_vix_level` and `rv_iv_spread` both need the same VIX level;
    it must be read once and threaded through, not recomputed (each read
    goes to disk via `bars_asof`)."""
    import te.ml.dataset as dataset_module

    calls: list[dt.datetime] = []
    real = dataset_module._india_vix_level

    def _counting(store_, symbol, as_of):  # noqa: ANN001, ANN202
        calls.append(as_of)
        return real(store_, symbol, as_of)

    monkeypatch.setattr(dataset_module, "_india_vix_level", _counting)

    as_of = _day(69) + dt.timedelta(hours=6)
    spec = FeatureSpec(name="vix-pair", version=1, columns=("india_vix_level", "rv_iv_spread"))
    result = build_training_set(as_of, spec, store, None, instrument=INSTRUMENT)

    assert len(calls) == 1, f"_india_vix_level recomputed {len(calls)} times for one feature row"
    # The two features stay consistent with each other and with that value.
    assert result["india_vix_level"] == pytest.approx(real(store, VIX_SYMBOL, as_of))


def test_day_of_week_is_cyclically_encoded_as_sin_and_cos(store: BarStore) -> None:
    """A raw ordinal 0-6 tells a tree that Friday(4) and Monday(0) are four
    units apart, when as trading days they are adjacent. sin/cos on a
    7-period circle encodes that adjacency truthfully."""
    import math

    # 2026-06-22 is a Monday (weekday 0); 2026-06-26 is a Friday (weekday 4).
    monday = dt.datetime(2026, 6, 22, 10, 0, tzinfo=IST)
    friday = dt.datetime(2026, 6, 26, 10, 0, tzinfo=IST)
    assert monday.weekday() == 0
    assert friday.weekday() == 4

    spec = FeatureSpec(name="dow", version=1, columns=("day_of_week_sin", "day_of_week_cos"))
    monday_row = build_training_set(monday, spec, store, None, instrument=INSTRUMENT)
    friday_row = build_training_set(friday, spec, store, None, instrument=INSTRUMENT)

    assert monday_row["day_of_week_sin"] == pytest.approx(math.sin(0.0))
    assert monday_row["day_of_week_cos"] == pytest.approx(math.cos(0.0))
    assert friday_row["day_of_week_sin"] == pytest.approx(math.sin(2 * math.pi * 4 / 7))
    assert friday_row["day_of_week_cos"] == pytest.approx(math.cos(2 * math.pi * 4 / 7))

    # Every encoded point lies on the unit circle.
    for row in (monday_row, friday_row):
        assert row["day_of_week_sin"] ** 2 + row["day_of_week_cos"] ** 2 == pytest.approx(1.0)


def test_cyclic_day_of_week_makes_sunday_and_monday_adjacent(store: BarStore) -> None:
    """The property a raw ordinal cannot express: the wrap-around distance
    between weekday 6 and weekday 0 equals the distance between any other
    adjacent pair."""
    import math

    def _point(day: int) -> tuple[float, float]:
        angle = 2 * math.pi * day / 7
        return math.sin(angle), math.cos(angle)

    def _distance(a: int, b: int) -> float:
        (sa_, ca) = _point(a)
        (sb, cb) = _point(b)
        return math.hypot(sa_ - sb, ca - cb)

    assert _distance(6, 0) == pytest.approx(_distance(0, 1))
    assert _distance(6, 0) == pytest.approx(_distance(3, 4))


def test_rv_iv_spread_uses_the_underlying_index_not_the_option_premium(store: BarStore) -> None:
    """The realized-vol leg must come from the UNDERLYING INDEX's price
    series, not the option premium's.

    An option premium's volatility is amplified by the option's leverage
    relative to its underlying (commonly 5-20x for OTM strikes), so an
    RV computed from the premium is dominated by WHICH STRIKE was traded
    rather than by any real RV/IV relationship — subtracting India VIX (the
    implied vol of the NIFTY INDEX) from it compares two dimensionally
    incomparable quantities.
    """
    import te.ml.dataset as dataset_module

    as_of = _day(69) + dt.timedelta(hours=6)
    symbols_read: list[str] = []
    real_rv = dataset_module._realized_vol_pct

    def _recording(store_, symbol, as_of_, **kwargs):  # noqa: ANN001, ANN202
        symbols_read.append(symbol)
        return real_rv(store_, symbol, as_of_, **kwargs)

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(dataset_module, "_realized_vol_pct", _recording)
    try:
        spec = FeatureSpec(name="rv-only", version=1, columns=("rv_iv_spread",))
        build_training_set(as_of, spec, store, None, instrument=INSTRUMENT)
    finally:
        monkeypatch.undo()

    assert symbols_read == [UNDERLYING], (
        f"realized vol was computed from {symbols_read!r}; it must read the underlying index {UNDERLYING!r}, "
        f"never the option premium {INSTRUMENT!r}"
    )


def test_rv_iv_spread_is_a_real_number_for_a_freshly_listed_contract(store: BarStore) -> None:
    """A weekly-expiry option that has only existed a few days has almost no
    premium history, so the old premium-based RV hit its `len(closes) < 3`
    guard and returned NaN on most rows. Reading the underlying — which has
    a long continuous history — makes the feature actually available."""
    as_of = _day(69) + dt.timedelta(hours=6)
    young_contract = "NIFTY30JUN2626500PE"  # no bars recorded for it at all
    spec = FeatureSpec(name="rv-only", version=1, columns=("rv_iv_spread",))

    result = build_training_set(as_of, spec, store, None, instrument=young_contract)
    assert not pd.isna(result["rv_iv_spread"])


# ---------------------------------------------------------------------------
# n=0 / thin-history: NaN ("unknown"), never a fabricated real-looking number
# ---------------------------------------------------------------------------


def test_iv_rank_is_nan_not_a_fabricated_midpoint_on_an_empty_store(tmp_path: Path) -> None:
    """`honest-metrics`/`no number may be displayed that hasn't been earned`:
    an empty VIX history must read as `NaN` ("unknown vol regime"), never as
    `0.5` ("vol is exactly mid-range") — a real-looking value fed straight
    into the model."""
    empty_store = BarStore(tmp_path / "empty_bars")
    as_of = _day(0)
    spec = FeatureSpec(name="iv-only", version=1, columns=("iv_rank_60d",))

    result = build_training_set(as_of, spec, empty_store, None, instrument=INSTRUMENT)

    assert math.isnan(result["iv_rank_60d"])


def test_iv_rank_is_nan_with_only_a_single_close(tmp_path: Path) -> None:
    """A single VIX close cannot form a percentile rank — must stay `NaN`,
    never silently rank the one point against itself."""
    thin_store = BarStore(tmp_path / "thin_bars")
    thin_store.append(pd.DataFrame([_bar(VIX_SYMBOL, _day(0), c=15.0)], columns=list(BAR_COLUMNS)))
    as_of = _day(0) + dt.timedelta(hours=6)
    spec = FeatureSpec(name="iv-only", version=1, columns=("iv_rank_60d",))

    result = build_training_set(as_of, spec, thin_store, None, instrument=INSTRUMENT)

    assert math.isnan(result["iv_rank_60d"])


def test_india_vix_level_is_nan_on_an_empty_store(tmp_path: Path) -> None:
    """No VIX bar recorded yet must read as `NaN`, not a fabricated level."""
    empty_store = BarStore(tmp_path / "empty_bars")
    as_of = _day(0)
    spec = FeatureSpec(name="vix-only", version=1, columns=("india_vix_level",))

    result = build_training_set(as_of, spec, empty_store, None, instrument=INSTRUMENT)

    assert math.isnan(result["india_vix_level"])


def test_realized_vol_leg_of_rv_iv_spread_is_nan_with_fewer_than_3_closes(tmp_path: Path) -> None:
    """`rv_iv_spread`'s realized-vol leg needs >= 3 closes to form even one
    log return with a defined stdev; below that it must stay `NaN` rather
    than silently reading as `-vix` (what a fabricated `rv=0.0` would
    produce)."""
    thin_store = BarStore(tmp_path / "thin_bars")
    # VIX has plenty of history (so `iv_proxy` is real); the UNDERLYING has
    # only 2 closes — one short of `_realized_vol_pct`'s 3-close floor.
    rows = [_bar(VIX_SYMBOL, _day(i), c=15.0) for i in range(10)]
    rows += [_bar(UNDERLYING, _day(i), c=24_000.0 + i) for i in range(2)]
    thin_store.append(pd.DataFrame(rows, columns=list(BAR_COLUMNS)))
    as_of = _day(9) + dt.timedelta(hours=6)
    spec = FeatureSpec(name="rv-only", version=1, columns=("rv_iv_spread",))

    result = build_training_set(as_of, spec, thin_store, None, instrument=INSTRUMENT)

    assert math.isnan(result["rv_iv_spread"])


def test_rv_iv_spread_is_nan_on_an_entirely_empty_store(tmp_path: Path) -> None:
    """No bars recorded for either leg: must stay `NaN`, not `-0.0` or any
    other fabricated value."""
    empty_store = BarStore(tmp_path / "empty_bars")
    as_of = _day(0)
    spec = FeatureSpec(name="rv-only", version=1, columns=("rv_iv_spread",))

    result = build_training_set(as_of, spec, empty_store, None, instrument=INSTRUMENT)

    assert math.isnan(result["rv_iv_spread"])


def test_daily_close_lookbacks_yield_the_requested_number_of_trading_closes(store: BarStore) -> None:
    """The lookback used to be a CALENDAR-day `timedelta` passed straight to
    `bars_asof`, so a "20-day" realized-vol window saw only ~14 actual
    trading closes and a "60-day" IV-rank window only ~42. Requesting N
    trading days must now actually yield N closes when N are available."""
    import te.ml.dataset as dataset_module

    as_of = _day(69) + dt.timedelta(hours=6)

    # The fixture writes a close on EVERY calendar day, so "trading closes"
    # available is the full history; asking for 20 must return exactly 20.
    closes = dataset_module._trailing_trading_closes(store, VIX_SYMBOL, as_of, n_trading_days=20)
    assert len(closes) == 20

    closes_60 = dataset_module._trailing_trading_closes(store, VIX_SYMBOL, as_of, n_trading_days=60)
    assert len(closes_60) == 60
