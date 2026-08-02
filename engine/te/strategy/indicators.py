"""Causal indicators shared by every strategy.

### The rule this module exists to enforce

Every function here returns a series where **row `i` depends only on rows
`<= i`**. That is the whole point of putting them in one place: lookahead is
the defect that makes a backtest profitable and worthless, it is nearly
invisible on inspection, and it is introduced by accident in exactly these
few lines of pandas.

The classic mistake is one missing `.shift(1)`:

    df["h"].rolling(20).max()          # includes the CURRENT bar
    df["h"].rolling(20).max().shift(1) # the previous 20 bars — correct

"Close above the 20-bar high" computed the first way is partly a statement
about the close itself, so it fires far more often and far more favourably
than the rule a live engine could ever run. Written once here, tested once,
and reused by thirty strategies, that mistake can be made in one place
instead of thirty.

Point-in-time correctness has TWO layers in this codebase and they are not
interchangeable. `te.data.asof.bars_asof` decides which bars a strategy is
allowed to see at all (`close_ts <= as_of AND ingested_at <= as_of`). These
functions decide that, given a frame of already-visible bars, no indicator
smuggles a later row into an earlier value. A strategy needs both.
"""

from __future__ import annotations

import pandas as pd

#: NSE/BSE regular session open, in IST. Used to measure "minutes since the
#: open" without every strategy re-deriving it.
SESSION_OPEN_HOUR = 9
SESSION_OPEN_MINUTE = 15


def minutes_from_open(session: pd.DataFrame) -> pd.Series:
    """Minutes elapsed since 09:15 IST for each bar.

    Computed from the session's own first bar rather than a hardcoded clock
    time, so a shortened session or the Muhurat evening session still
    produces sensible values instead of large negatives.
    """
    ist = session["ist"]
    open_ts = ist.iloc[0].replace(hour=SESSION_OPEN_HOUR, minute=SESSION_OPEN_MINUTE, second=0, microsecond=0)
    return (ist - open_ts).dt.total_seconds() / 60


def opening_range(session: pd.DataFrame, minutes: int) -> tuple[float, float] | None:
    """`(high, low)` of the first `minutes` of the session, or `None` if it
    has not finished forming yet.

    Returning `None` rather than a partial range matters: a range computed
    from three bars of a sixty-minute window is a different, much narrower
    thing, and using it would make the strategy fire early on a boundary it
    would never have traded live.
    """
    opening = session[session["minutes_from_open"] < minutes]
    if len(opening) < 3 or session["minutes_from_open"].iloc[-1] < minutes:
        return None
    return float(opening["h"].max()), float(opening["l"].min())


def session_average(session: pd.DataFrame) -> pd.Series:
    """Running average price since the open — VWAP where volume exists, a
    time-weighted average where it does not.

    **An index has no traded volume of its own; only its derivatives do.**
    Every stored NIFTY bar carries `v=0` (verified against 1,821,133 rows on
    2026-08-01), which is a real property of the NSE index feed rather than
    a gap in our recording. Dividing by a cumulative volume of zero yields an
    all-null column and, in the first draft of the strategy sweep, an
    unrelated-looking pandas dtype crash.

    Falling back to a time-weighted average is the honest substitute — with
    no volume information, every minute weighs the same — but it IS a
    different statistic, which is why callers name these rules `twap_*`
    rather than `vwap_*`.
    """
    typical = (session["h"] + session["l"] + session["c"]) / 3
    volume = session["v"]
    if float(volume.sum()) > 0:
        return (typical * volume).cumsum() / volume.cumsum()
    return typical.expanding().mean()


def prior_rolling(series: pd.Series, window: int, how: str) -> pd.Series:
    """Rolling statistic over the `window` bars ENDING BEFORE each bar.

    The `.shift(1)` is the entire reason this is a named function rather
    than an inline expression — see the module docstring.
    """
    return getattr(series.rolling(window), how)().shift(1)


def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """Wilder's RSI. Uses `ewm(adjust=False)`, which is the recursive form
    Wilder defined and what every charting package shows — the simple
    rolling mean version drifts away from it and would not match what the
    user sees on their broker's chart."""
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / period, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / period, adjust=False).mean()
    return 100 - 100 / (1 + gain / loss.replace(0, pd.NA))


def stochastic(session: pd.DataFrame, period: int = 14) -> pd.Series:
    """Stochastic %K over `period` bars, using windows that INCLUDE the
    current bar's own high/low.

    Unlike `prior_rolling`, that is correct here and not a lookahead: %K
    asks "where does the current close sit within the recent range", and the
    current bar's own extremes are known at its close. The distinction is
    worth stating because the two look almost identical in code.
    """
    high = session["h"].rolling(period).max()
    low = session["l"].rolling(period).min()
    span = (high - low).replace(0, pd.NA)
    return (session["c"] - low) / span * 100


def true_range(session: pd.DataFrame) -> pd.Series:
    """True range per bar: the largest of the bar's own range and its gaps
    from the previous close."""
    previous_close = session["c"].shift(1)
    return pd.concat(
        [
            session["h"] - session["l"],
            (session["h"] - previous_close).abs(),
            (session["l"] - previous_close).abs(),
        ],
        axis=1,
    ).max(axis=1)


def atr(session: pd.DataFrame, period: int = 14) -> pd.Series:
    """Average true range, Wilder-smoothed and shifted so a bar is never
    inside its own volatility estimate."""
    return true_range(session).ewm(alpha=1 / period, adjust=False).mean().shift(1)


def macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> tuple[pd.Series, pd.Series]:
    """`(macd_line, signal_line)`. Exponential means are causal by
    construction — each value depends only on earlier ones."""
    line = close.ewm(span=fast, adjust=False).mean() - close.ewm(span=slow, adjust=False).mean()
    return line, line.ewm(span=signal, adjust=False).mean()


def supertrend(session: pd.DataFrame, *, period: int = 10, multiplier: float = 3.0) -> pd.Series:
    """SuperTrend direction: `+1` uptrend, `-1` downtrend.

    Included because it is the single most widely used trend indicator among
    Indian retail traders, so a strategy library that omitted it would look
    incomplete to the person reading the page — and because "popular" is a
    reason to TEST something, never a reason to believe it.

    Genuinely recursive: each bar's band and direction depend on the previous
    bar's, so this is an explicit loop rather than a vectorised expression.
    The loop only ever reads index `i-1`, which is what keeps it causal.
    """
    band = atr(session, period) * multiplier
    midpoint = (session["h"] + session["l"]) / 2
    upper = (midpoint + band).to_numpy()
    lower = (midpoint - band).to_numpy()
    close = session["c"].to_numpy()

    direction = [1] * len(session)
    final_upper = list(upper)
    final_lower = list(lower)
    for i in range(1, len(session)):
        if pd.isna(upper[i]) or pd.isna(lower[i]):
            direction[i] = direction[i - 1]
            continue
        # Bands may only tighten while the trend holds — that ratchet is
        # what stops SuperTrend flipping on every wiggle.
        final_upper[i] = (
            min(upper[i], final_upper[i - 1]) if close[i - 1] <= final_upper[i - 1] else upper[i]
        )
        final_lower[i] = (
            max(lower[i], final_lower[i - 1]) if close[i - 1] >= final_lower[i - 1] else lower[i]
        )
        if close[i] > final_upper[i - 1]:
            direction[i] = 1
        elif close[i] < final_lower[i - 1]:
            direction[i] = -1
        else:
            direction[i] = direction[i - 1]
    return pd.Series(direction, index=session.index)


def pivot_levels(previous_high: float, previous_low: float, previous_close: float) -> dict[str, float]:
    """Classic floor-trader pivots from the PREVIOUS session."""
    pivot = (previous_high + previous_low + previous_close) / 3
    span = previous_high - previous_low
    return {
        "pivot": pivot,
        "r1": 2 * pivot - previous_low,
        "s1": 2 * pivot - previous_high,
        "r2": pivot + span,
        "s2": pivot - span,
    }


def camarilla_levels(previous_high: float, previous_low: float, previous_close: float) -> dict[str, float]:
    """Camarilla levels from the PREVIOUS session.

    Widely used in Indian intraday trading, where H3/L3 are treated as the
    reversal band and H4/L4 as the breakout band.
    """
    span = previous_high - previous_low
    return {
        "h3": previous_close + span * 1.1 / 4,
        "h4": previous_close + span * 1.1 / 2,
        "l3": previous_close - span * 1.1 / 4,
        "l4": previous_close - span * 1.1 / 2,
    }
