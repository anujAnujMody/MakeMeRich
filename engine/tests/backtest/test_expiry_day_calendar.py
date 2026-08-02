"""`ExpiryDayOnly` must use the REAL expiry calendar when a backtest
supplies one, not its weekday fallback.

Found on 2026-08-01: the strategy's `expiry_weekday` defaults to Tuesday,
but of NIFTY's 125 real expiries in the archive, 83 fell on Thursday and
only 32 on Tuesday — the weekday moved partway through the data (Thursday
until Sep 2025, then Tuesday). The first backtest of this rule (117 trades,
run against the weekday heuristic) was therefore contaminated: a large
share of its "expiry day" trades were ordinary Tuesdays that happened to
share a weekday with the recent expiry convention, not real expiries.

`te.backtest.strategy_lab._derive_session` now attaches a real
`is_expiry_day` column from `OptionContractIndex.expiries` whenever a
backtest passes `expiry_dates`, and `ExpiryDayOnly` prefers that column when
present. These tests pin both halves: the column is read correctly, and the
weekday fallback still works for the live path, which has no calendar wired.
"""

from __future__ import annotations

import datetime as dt

import pandas as pd

from te.backtest.strategy_lab import _derive_session
from te.domain.clock import IST
from te.strategy.registry import get


def _bars(day: dt.date, count: int = 375) -> pd.DataFrame:
    rows = []
    for minute in range(count):
        ts = dt.datetime.combine(day, dt.time(9, 15), tzinfo=IST) + dt.timedelta(minutes=minute)
        rows.append(
            {
                "symbol": "NIFTY",
                "exchange": "NSE_INDEX",
                "event_ts": ts.astimezone(dt.UTC),
                "interval": "1m",
                "o": 24_000.0,
                "h": 24_005.0,
                "l": 23_995.0,
                "c": 24_000.0 + minute * 0.1,
                "v": 0.0,
                "oi": 0,
                "ingested_at": ts.astimezone(dt.UTC),
                "source": "test",
            }
        )
    return pd.DataFrame(rows)


class _InMemoryStore:
    """Serves the live `_session_frame` path from one preloaded day-frame."""

    def __init__(self, frame: pd.DataFrame) -> None:
        self._frame = frame

    def read(
        self,
        symbol: str,
        start: dt.datetime,
        end: dt.datetime,
        interval: str,
        ingested_before: dt.datetime | None = None,
    ) -> pd.DataFrame:
        del symbol, interval, ingested_before
        df = self._frame
        mask = (df["event_ts"] >= pd.Timestamp(start)) & (df["event_ts"] <= pd.Timestamp(end))
        return df.loc[mask].reset_index(drop=True)


#: A real Thursday that was NEVER a Tuesday-convention expiry.
_REAL_THURSDAY_EXPIRY = dt.date(2024, 3, 14)  # a Thursday
#: An ordinary Tuesday in the same period that was NOT an expiry.
_ORDINARY_TUESDAY = dt.date(2024, 3, 12)


def test_a_real_thursday_expiry_is_recognised_via_the_calendar() -> None:
    """The whole point: a Thursday the weekday heuristic would reject
    outright must be recognised as an expiry when the real calendar says so."""
    prebuilt = _derive_session(
        _bars(_REAL_THURSDAY_EXPIRY), None, expiry_dates=frozenset({_REAL_THURSDAY_EXPIRY})
    )
    assert prebuilt is not None
    session = prebuilt.frame
    assert "is_expiry_day" in session.columns
    assert bool(session["is_expiry_day"].iloc[0]) is True


def test_an_ordinary_tuesday_is_correctly_rejected_via_the_calendar() -> None:
    """The regression this whole fix exists for: the OLD weekday default
    (Tuesday) would have accepted this day. The real calendar must not."""
    prebuilt = _derive_session(
        _bars(_ORDINARY_TUESDAY), None, expiry_dates=frozenset({_REAL_THURSDAY_EXPIRY})
    )
    assert prebuilt is not None
    assert bool(prebuilt.frame["is_expiry_day"].iloc[0]) is False


def test_without_expiry_dates_no_column_is_added() -> None:
    """Backward compatibility with every other caller of `_derive_session`,
    and the reason the live path falls back safely: omitting the argument
    must not add a column that changes behaviour."""
    prebuilt = _derive_session(_bars(_REAL_THURSDAY_EXPIRY), None)
    assert prebuilt is not None
    assert "is_expiry_day" not in prebuilt.frame.columns


def test_the_strategy_trusts_the_real_calendar_over_weekday() -> None:
    """End to end: `ExpiryDayOnly` must fire on the real Thursday expiry
    (which its own weekday default of Tuesday would reject) once the
    calendar column is present, and must NOT fire on the ordinary Tuesday
    even though that matches its weekday default."""
    from te.strategy.context import StrategyContext
    from te.strategy.session_rule import _PREBUILT_KEY

    strategy = get("expiry_day_only")

    thursday_prebuilt = _derive_session(
        _bars(_REAL_THURSDAY_EXPIRY), None, expiry_dates=frozenset({_REAL_THURSDAY_EXPIRY})
    )
    assert thursday_prebuilt is not None
    ctx = StrategyContext(
        store=None,  # type: ignore[arg-type]
        instrument="NIFTY",
        exchange="NSE_INDEX",
        as_of=dt.datetime.combine(_REAL_THURSDAY_EXPIRY, dt.time(14, 0), tzinfo=IST),
    )
    ctx.state[_PREBUILT_KEY] = thursday_prebuilt
    evaluation = strategy.evaluate(ctx)
    expiry_condition = next(c for c in evaluation.conditions if c.label == "today is an expiry day")
    assert expiry_condition.passed is True, "the real Thursday expiry must pass the calendar check"

    tuesday_prebuilt = _derive_session(
        _bars(_ORDINARY_TUESDAY), None, expiry_dates=frozenset({_REAL_THURSDAY_EXPIRY})
    )
    assert tuesday_prebuilt is not None
    ctx2 = StrategyContext(
        store=None,  # type: ignore[arg-type]
        instrument="NIFTY",
        exchange="NSE_INDEX",
        as_of=dt.datetime.combine(_ORDINARY_TUESDAY, dt.time(14, 0), tzinfo=IST),
    )
    ctx2.state[_PREBUILT_KEY] = tuesday_prebuilt
    strategy2 = get("expiry_day_only")
    evaluation2 = strategy2.evaluate(ctx2)
    expiry_condition2 = next(c for c in evaluation2.conditions if c.label == "today is an expiry day")
    assert expiry_condition2.passed is False, (
        "an ordinary Tuesday must be rejected even though it matches the old weekday default"
    )


def test_without_a_calendar_the_rule_stands_down_instead_of_guessing() -> None:
    """The weekday fallback is GONE, deliberately.

    It used to fire here — 2026-01-06 is a Tuesday, which was the old
    default `expiry_weekday`. That guess mismatched 83 of 125 real NIFTY
    expiries, so a rule with no calendar must refuse to trade rather than
    trade on a coin flip dressed up as a condition.
    """
    strategy = get("expiry_day_only")
    prebuilt = _derive_session(_bars(dt.date(2026, 1, 6)), None)  # a Tuesday, no expiry_dates given
    assert prebuilt is not None
    assert "is_expiry_day" not in prebuilt.frame.columns

    from te.strategy.context import StrategyContext
    from te.strategy.session_rule import _PREBUILT_KEY

    ctx = StrategyContext(
        store=None,  # type: ignore[arg-type]
        instrument="NIFTY",
        exchange="NSE_INDEX",
        as_of=dt.datetime.combine(dt.date(2026, 1, 6), dt.time(14, 0), tzinfo=IST),
    )
    ctx.state[_PREBUILT_KEY] = prebuilt
    evaluation = strategy.evaluate(ctx)
    assert evaluation.verdict == "skipped"
    assert "refusing to guess" in evaluation.reason
    expiry_condition = next(c for c in evaluation.conditions if c.label == "today is an expiry day")
    assert expiry_condition.passed is False


def test_the_live_path_gets_the_column_from_the_context() -> None:
    """The other half of the fix: the calendar reaches the LIVE frame
    builder through `StrategyContext.expiry_dates`, so live and backtest
    answer the question the same way instead of one guessing."""
    from te.strategy.context import StrategyContext
    from te.strategy.session_rule import SessionRule

    day = _REAL_THURSDAY_EXPIRY
    store = _InMemoryStore(_bars(day))
    ctx = StrategyContext(
        store=store,  # type: ignore[arg-type]
        instrument="NIFTY",
        exchange="NSE_INDEX",
        as_of=dt.datetime.combine(day, dt.time(14, 0), tzinfo=IST),
        expiry_dates=frozenset({day}),
    )
    frame = SessionRule._session_frame(get("expiry_day_only"), ctx)  # type: ignore[arg-type]
    assert frame is not None
    assert "is_expiry_day" in frame.columns, "the live builder must attach the calendar column too"
    assert bool(frame["is_expiry_day"].iloc[0]) is True
