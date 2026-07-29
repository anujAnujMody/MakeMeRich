"""`build_training_set()` — THE ONLY path from bars/data to a feature
vector for the secondary (meta-labeling) model. Both offline training AND
live inference call this exact same function — training calls it once per
historical ORB firing timestamp (`for_inference=False`), live inference
calls it once with `as_of=now()` (`for_inference=True`) — so
online/offline skew is structurally impossible: there is no second code
path that could compute a feature differently.

Every bar this module reads goes through `te.data.asof.bars_asof`, never a
raw `BarStore.read()` — the point-in-time gate applies to `te.ml` exactly as
it does to `te.strategy`.

**Known approximation, flagged clearly (to refine once real option-chain/IV
surface data is recorded — not blocking this phase per the plan):**
`rv_iv_spread` needs a realized-vol figure (computable today, directly from
recorded bars) and an implied-vol figure. Full IV surface / per-strike Greeks
data isn't recorded this early in the project, so implied vol is
approximated as the **India VIX level itself**, used as a flat ATM-IV proxy
(the plan's suggested simplification: "using ATM IV proxy from VIX scaled by
moneyness" — this module does the *unscaled* version, i.e. no moneyness
adjustment yet; scaling by (strike/spot) moneyness is a documented future
refinement once option-chain data exists). Realized vol is the annualized
stdev of the **underlying index's** daily log returns — the same underlying
India VIX itself prices, so the two legs of the spread are dimensionally
comparable — expressed in the same "vol points" units as VIX (percent).
See `_rv_iv_spread` for why the option premium's own series cannot be used.

All trading-session lookbacks go through `_trailing_trading_closes`, never a
raw calendar-day `timedelta`: `bars_asof` windows by elapsed time, so a
"20-day" window expressed in calendar days silently yields only ~14 closes.

Similarly, `india_vix_term_slope` needs near/next-month VIX futures bars;
if the caller doesn't supply symbols for them (or no bars are recorded yet
under those symbols as of `as_of`), the slope is reported as `float("nan")`
rather than a fabricated number — never silently defaulted to a plausible-
looking value, per the plan's "no number may be displayed that hasn't been
earned" discipline.

**How those NaNs are handled downstream:** they are NOT imputed. They pass
through to XGBoost's native missing-value handling (`te.ml.train` fits a
bare `XGBClassifier`; there is no sklearn `Pipeline` and no imputer anywhere
in `te.ml`). XGBoost learns a default split direction for missing values at
each node, which is both leak-free and better suited than median imputation
to features that are STRUCTURALLY missing — an absent `india_vix_term_slope`
means "no VIX futures bars were recorded", which is information, not a value
to be guessed at.
"""

from __future__ import annotations

import datetime as dt
import math
from collections.abc import Sequence

import pandas as pd
from sqlalchemy.orm import Session, sessionmaker

from te.data.asof import bars_asof
from te.data.barstore import BarStore
from te.domain.clock import DEFAULT_SESSION, IST
from te.domain.symbols import parse_option_symbol
from te.ml.featurespec import PRIMARY_VOCABULARY, FeatureSpec

DEFAULT_VIX_SYMBOL = "INDIAVIX"
_ANNUALIZATION_TRADING_DAYS = 252

#: 7/5 — one trading week per calendar week. Used to widen a
#: trading-session-count lookback into the calendar-day window `bars_asof`
#: actually takes.
_CALENDAR_DAYS_PER_TRADING_DAY = 7 / 5

#: Extra calendar days on top of the weekend adjustment, covering NSE/BSE's
#: ~10-12 trading holidays a year plus any multi-day closure. Over-requesting
#: is free (the window is trimmed to the requested bar count afterwards);
#: under-requesting silently shortens every window.
_HOLIDAY_CUSHION_DAYS = 12

#: Trading-session counts for the two windowed features.
_REALIZED_VOL_TRADING_DAYS = 20
_IV_RANK_TRADING_DAYS = 60


def _reassert_disjointness(spec: FeatureSpec) -> None:
    """`FeatureSpec.__post_init__` already checked this at construction
    time; this call-time re-assertion is what the plan means by "checked at
    import time and again inside `build_training_set`" — it protects against
    a `FeatureSpec` that was mutated via `object.__setattr__` (frozen
    dataclasses can still be bypassed that way) or a hand-rolled spec that
    skipped the constructor entirely (e.g. built via
    `FeatureSpec.__new__`)."""
    overlap = set(spec.columns) & PRIMARY_VOCABULARY
    if overlap:
        raise ValueError(f"secondary spec leaks primary features: {sorted(overlap)}")


def _minutes_from_open(as_of: dt.datetime) -> float:
    local = as_of.astimezone(IST)
    market_open = dt.datetime.combine(local.date(), DEFAULT_SESSION.start, tzinfo=IST)
    return (local - market_open).total_seconds() / 60.0


def _day_of_week_sin_cos(as_of: dt.datetime) -> tuple[float, float]:
    """`day_of_week` as a point on a 7-period circle.

    A raw ordinal (0-6) asserts both a false ORDERING ("Wednesday > Tuesday")
    and a false DISTANCE: Friday (4) and Monday (0) are adjacent trading days
    but sit four units apart, further than any genuinely adjacent pair. The
    sin/cos pair makes the wrap-around adjacency real, and every point lies
    on the unit circle so neither column dominates the other in scale."""
    angle = 2.0 * math.pi * as_of.astimezone(IST).weekday() / 7.0
    return math.sin(angle), math.cos(angle)


def _dte(as_of: dt.datetime, instrument: str) -> float:
    parsed = parse_option_symbol(instrument)
    return float((parsed.expiry - as_of.astimezone(IST).date()).days)


def _daily_closes(store: BarStore, symbol: str, as_of: dt.datetime, lookback_days: int) -> pd.Series:
    """Raw CALENDAR-day window. Prefer `_trailing_trading_closes` for any
    window whose size is meant to be a count of trading sessions."""
    bars = bars_asof(store, symbol, as_of, dt.timedelta(days=lookback_days), interval="1d")
    if bars.empty:
        return pd.Series(dtype=float)
    return bars.set_index("event_ts")["c"].astype(float)


def _trailing_trading_closes(store: BarStore, symbol: str, as_of: dt.datetime, *, n_trading_days: int) -> pd.Series:
    """The last `n_trading_days` daily closes at or before `as_of` (fewer if
    that much history isn't recorded yet).

    `bars_asof` windows by ELAPSED TIME, not by bar count, so a lookback
    meant to mean "20 trading sessions" cannot be expressed as
    `timedelta(days=20)`: weekends alone reduce that to ~14 closes, and NSE's
    ~10-12 annual holidays trim it further. So request a deliberately
    over-wide calendar window (`_CALENDAR_DAYS_PER_TRADING_DAY` plus a fixed
    holiday cushion) and TRIM to the requested count from the recent end."""
    calendar_days = math.ceil(n_trading_days * _CALENDAR_DAYS_PER_TRADING_DAY) + _HOLIDAY_CUSHION_DAYS
    closes = _daily_closes(store, symbol, as_of, lookback_days=calendar_days)
    if closes.empty:
        return closes
    return closes.iloc[-n_trading_days:]


def _india_vix_level(store: BarStore, vix_symbol: str, as_of: dt.datetime) -> float:
    closes = _trailing_trading_closes(store, vix_symbol, as_of, n_trading_days=1)
    if closes.empty:
        return float("nan")
    return float(closes.iloc[-1])


def _iv_rank_60d(store: BarStore, vix_symbol: str, as_of: dt.datetime) -> float:
    """Percentile rank (0..1) of the latest VIX close within its own
    trailing 60-TRADING-day window — a real measured value, `NaN` (not a
    fabricated midpoint) when fewer than 2 days of history exist yet."""
    closes = _trailing_trading_closes(store, vix_symbol, as_of, n_trading_days=_IV_RANK_TRADING_DAYS)
    if len(closes) < 2:
        return float("nan")
    latest = closes.iloc[-1]
    return float((closes <= latest).sum() / len(closes))


def _india_vix_term_slope(
    store: BarStore, as_of: dt.datetime, *, near_symbol: str | None, next_symbol: str | None
) -> float:
    if near_symbol is None or next_symbol is None:
        return float("nan")
    near = _daily_closes(store, near_symbol, as_of, lookback_days=5)
    next_ = _daily_closes(store, next_symbol, as_of, lookback_days=5)
    if near.empty or next_.empty or near.iloc[-1] == 0:
        return float("nan")
    return float((next_.iloc[-1] - near.iloc[-1]) / near.iloc[-1])


def _realized_vol_pct(
    store: BarStore, symbol: str, as_of: dt.datetime, n_trading_days: int = _REALIZED_VOL_TRADING_DAYS
) -> float:
    """Annualized realized vol (in percentage-point units, comparable to
    VIX) of `symbol`'s own daily closes' log returns, over the last
    `n_trading_days` sessions.

    Close-to-close rather than a range-based (Garman-Klass) estimator: a
    deliberate choice, not an oversight — the intraday highs/lows a
    range estimator needs are the least reliable prints in an illiquid
    series."""
    closes = _trailing_trading_closes(store, symbol, as_of, n_trading_days=n_trading_days)
    if len(closes) < 3:
        return float("nan")
    log_returns = (closes / closes.shift(1)).apply(math.log).dropna()
    if len(log_returns) < 2:
        return float("nan")
    daily_std = float(log_returns.std(ddof=1))
    return daily_std * math.sqrt(_ANNUALIZATION_TRADING_DAYS) * 100.0


def _rv_iv_spread(store: BarStore, instrument: str, as_of: dt.datetime, *, iv_proxy: float) -> float:
    """Realized vol of the UNDERLYING INDEX minus the India VIX level.

    **Both legs must describe the same underlying.** India VIX is the
    implied volatility of the NIFTY INDEX, so the realized leg is computed
    from the index's own recorded price series — resolved from the option
    symbol via `te.domain.symbols.parse_option_symbol().base`, which yields
    exactly the ticker (`NIFTY`/`BANKNIFTY`/`SENSEX`/`BANKEX`) that
    `te.engine.scheduler`'s WS subscription list already records bars under.
    No new caller-supplied parameter is needed.

    This previously used the OPTION PREMIUM's own price series, which is not
    comparable: premium volatility is amplified by the option's leverage
    relative to its underlying (commonly 5-20x for OTM strikes), so the
    "spread" was dominated by which strike happened to be traded rather than
    by any RV/IV relationship. It was also usually NaN — a weekly-expiry
    contract has existed for only a few days, so the premium series rarely
    cleared the 3-close minimum, whereas the index series is continuous.

    `iv_proxy` is the already-computed `_india_vix_level` for this same
    `as_of` — passed in rather than recomputed, since every VIX read goes
    to disk through `bars_asof`."""
    underlying = parse_option_symbol(instrument).base
    rv = _realized_vol_pct(store, underlying, as_of)
    if math.isnan(rv) or math.isnan(iv_proxy):
        return float("nan")
    return rv - iv_proxy


def build_training_set(
    as_of: dt.datetime,
    spec: FeatureSpec,
    store: BarStore,
    db: sessionmaker[Session] | None,
    *,
    instrument: str,
    vix_symbol: str = DEFAULT_VIX_SYMBOL,
    vix_near_symbol: str | None = None,
    vix_next_symbol: str | None = None,
    for_inference: bool = False,
) -> pd.Series:
    """Builds one feature row for `instrument` at `as_of`, restricted to
    `spec.columns`. Called identically by training (once per historical ORB
    firing, `for_inference=False`) and live inference (`as_of=now()`,
    `for_inference=True`) — the SAME function, so there is no second
    computation path that could drift from this one.

    `db` is accepted (not currently read) to keep this call's signature
    stable for a later phase that may need to cross-reference persisted
    state (e.g. `engine_state`) while building a feature row — kept as an
    explicit parameter now rather than added later as a breaking change,
    per the plan's literal `build_training_set(as_of, spec, store, db,
    for_inference=False)` signature.

    Raises `ValueError` if `spec` leaks any `PRIMARY_VOCABULARY` column —
    re-asserted here even though `FeatureSpec.__post_init__` already checked
    it at construction time (see `_reassert_disjointness`'s docstring).
    """
    _reassert_disjointness(spec)
    del db, for_inference  # reserved for future symmetry — see docstring

    # Read once, used by BOTH `india_vix_level` and `rv_iv_spread`.
    vix_level = _india_vix_level(store, vix_symbol, as_of)
    dow_sin, dow_cos = _day_of_week_sin_cos(as_of)

    values: dict[str, float] = {
        "iv_rank_60d": _iv_rank_60d(store, vix_symbol, as_of),
        "india_vix_level": vix_level,
        "india_vix_term_slope": _india_vix_term_slope(
            store, as_of, near_symbol=vix_near_symbol, next_symbol=vix_next_symbol
        ),
        "rv_iv_spread": _rv_iv_spread(store, instrument, as_of, iv_proxy=vix_level),
        "day_of_week_sin": dow_sin,
        "day_of_week_cos": dow_cos,
        "minutes_from_open": _minutes_from_open(as_of),
        "dte": _dte(as_of, instrument),
    }

    missing = [c for c in spec.columns if c not in values]
    if missing:
        raise ValueError(f"build_training_set does not know how to compute columns: {missing}")

    return pd.Series({c: values[c] for c in spec.columns}, name=as_of.isoformat())


def build_training_matrix(
    rows: Sequence[tuple[dt.datetime, str]],
    spec: FeatureSpec,
    store: BarStore,
    db: sessionmaker[Session] | None,
    **kwargs: object,
) -> pd.DataFrame:
    """Convenience wrapper stacking `build_training_set` over many
    `(as_of, instrument)` pairs (e.g. every historical ORB firing) into one
    matrix — still calls `build_training_set` exactly once per row, so this
    remains "the only path from bars to features", not a second one."""
    series = [
        build_training_set(as_of, spec, store, db, instrument=instrument, for_inference=False, **kwargs)  # type: ignore[arg-type]
        for as_of, instrument in rows
    ]
    if not series:
        return pd.DataFrame(columns=spec.columns)
    return pd.DataFrame(series)
