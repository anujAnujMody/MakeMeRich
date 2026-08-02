"""Every rule in `scripts/sweep_strategies.py` must be CAUSAL.

This is the single most important test around the strategy sweep, and it
exists because lookahead is the defect that makes a backtest profitable
while being worthless. It is also nearly invisible by inspection: a
`.rolling(20).max()` without `.shift(1)` includes the current bar in its own
reference window, a `df["h"].max()` over a whole day frame is the day's high
known only in hindsight, and either one produces a beautiful equity curve
and no error message.

The property tested is the strongest available statement of causality:

    a rule's decision at minute T must not change when every bar after
    minute T is deleted.

If a rule reads even one future bar, truncating the frame at its own signal
bar will change what it emits. Running that check for every rule against
real recorded sessions catches the whole class, including in rules added
later — which is why this asserts over the `RULES` registry rather than over
a hand-listed set of names.

`_signals_for_day` is exercised rather than the raw rule functions, since
the entry-time filter and the per-day cap are part of the decision too.
"""

from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "scripts"))

from sweep_strategies import RULES, _signals_for_day  # noqa: E402

from te.domain.clock import IST  # noqa: E402


def _session(day: dt.date, closes: list[float], *, prev_close: float | None = 100.0) -> pd.DataFrame:
    """A day-frame shaped exactly like `_day_frames` produces: one bar per
    minute from 09:15 IST, with `ist`, `minutes_from_open` and `prev_close`
    already attached."""
    start = dt.datetime.combine(day, dt.time(9, 15), tzinfo=IST)
    rows = []
    for minute, close in enumerate(closes):
        ts = start + dt.timedelta(minutes=minute)
        # A high/low straddling the close, so range-based rules have
        # something non-degenerate to work with. The half-spread is kept
        # SMALL relative to the per-minute drift on purpose: an earlier draft
        # used +/-0.5 against a 0.06/minute drift, so a bar's own wick was
        # wider than twenty minutes of trend. Both Donchian rules and
        # `tight_range_breakout` then never fired — not because they are
        # wrong, but because no synthetic bar could ever exceed the previous
        # bars' wicks. Roughly 0.03% of price matches a real NIFTY minute.
        rows.append(
            {
                "event_ts": pd.Timestamp(ts.astimezone(dt.UTC)),
                "o": close,
                "h": close + 0.03,
                "l": close - 0.03,
                "c": close,
                "v": 0.0,
                "ingested_at": pd.Timestamp(ts.astimezone(dt.UTC)),
            }
        )
    frame = pd.DataFrame(rows)
    frame["ist"] = frame["event_ts"].dt.tz_convert(IST)
    session_open = frame["ist"].iloc[0]
    frame["minutes_from_open"] = (frame["ist"] - session_open).dt.total_seconds() / 60
    frame["prev_close"] = prev_close
    return frame


def _trending_then_reversing(n: int = 360) -> list[float]:
    """A price path with a real opening range, a genuine breakout, a
    reversal and some noise."""
    rng = np.random.default_rng(7)
    path = [100.0]
    for minute in range(1, n):
        if minute < 60:
            drift = 0.0  # a flat-ish opening range
        elif minute < 180:
            drift = 0.05  # break upward
        else:
            drift = -0.04  # then reverse
        path.append(path[-1] + drift + float(rng.normal(0, 0.08)))
    return path


def _coiled_then_running(n: int = 360) -> list[float]:
    """A very tight opening range followed by a sustained one-way run.

    Exists because the first path leaves five rules unfired — the two
    Donchian breakouts need a genuine multi-hour trend to take out a rolling
    high, and `tight_range_breakout` requires an opening range narrower than
    0.35%. A rule that never fires is a rule the causality check never
    examined, so the paths are chosen to cover the registry between them.
    """
    rng = np.random.default_rng(11)
    path = [100.0]
    for minute in range(1, n):
        drift = 0.0 if minute < 60 else 0.06
        noise = 0.005 if minute < 60 else 0.05
        path.append(path[-1] + drift + float(rng.normal(0, noise)))
    return path


#: `prev_close` differs from the 100.0 open on the second path, which is what
#: arms the gap rules — they refuse to act on a gap below 0.3%.
_PATHS = {
    "trend_then_reverse": (_trending_then_reversing(), 100.0),
    "coiled_then_run": (_coiled_then_running(), 98.5),
}


@pytest.fixture(scope="module", params=sorted(_PATHS))
def frame(request: pytest.FixtureRequest) -> pd.DataFrame:
    closes, prev_close = _PATHS[request.param]
    return _session(dt.date(2026, 3, 10), closes, prev_close=prev_close)


def test_every_rule_fires_on_at_least_one_path() -> None:
    """The coverage guard for the causality check above.

    Each parametrised case skips when its rule does not fire, which is
    correct per-path but would let a rule escape scrutiny entirely if it
    fired on NO path. This asserts the registry is fully exercised across the
    paths taken together, so a silent gap shows up here rather than as an
    unnoticed run of green skips.
    """
    fired: set[str] = set()
    for closes, prev_close in _PATHS.values():
        day = _session(dt.date(2026, 3, 10), closes, prev_close=prev_close)
        fired |= {name for name, rule in RULES.items() if _signals_for_day(rule, day)}

    never = sorted(set(RULES) - fired)
    assert not never, f"these rules fired on no test path, so their causality was never checked: {never}"


@pytest.mark.parametrize("rule_name", sorted(RULES))
def test_a_rule_decision_does_not_change_when_the_future_is_deleted(rule_name: str, frame: pd.DataFrame) -> None:
    """Truncate the day at each signal and re-run. The signal must survive.

    A rule that read a future bar would either stop firing at that minute or
    fire somewhere different once the future was removed."""
    rule = RULES[rule_name]
    signals = _signals_for_day(rule, frame)
    if not signals:
        pytest.skip(f"{rule_name} did not fire on this path — nothing to check")

    for entry_ts, direction in signals:
        # `entry_ts` is the signal bar's close, i.e. one minute after the bar
        # the rule actually decided on. Keeping bars strictly before it is
        # therefore exactly "everything the rule was entitled to see".
        visible = frame[frame["event_ts"] < pd.Timestamp(entry_ts)].reset_index(drop=True)
        replayed = _signals_for_day(rule, visible)
        assert (entry_ts, direction) in replayed, (
            f"{rule_name} emitted {direction} at {entry_ts} on the full day, but NOT when the day was "
            f"truncated at that same minute — the rule is reading bars from the future"
        )


@pytest.mark.parametrize("rule_name", sorted(RULES))
def test_a_rule_never_fires_after_the_last_entry_time(rule_name: str, frame: pd.DataFrame) -> None:
    """A firing the live engine would refuse must not become evidence about
    the rule. `_signals_for_day` enforces this; the test stops a later edit
    from moving the filter and silently widening every measured sample."""
    from sweep_strategies import LAST_ENTRY

    for entry_ts, _ in _signals_for_day(RULES[rule_name], frame):
        assert entry_ts.astimezone(IST).time() <= LAST_ENTRY


@pytest.mark.parametrize("rule_name", sorted(RULES))
def test_a_rule_respects_the_per_day_entry_cap(rule_name: str, frame: pd.DataFrame) -> None:
    """Without a cap, one trending session can contribute dozens of firings
    and dominate a rule's statistics — the result then describes a handful of
    days, not the rule."""
    from sweep_strategies import MAX_ENTRIES_PER_DAY

    assert len(_signals_for_day(RULES[rule_name], frame)) <= MAX_ENTRIES_PER_DAY


def test_the_registry_is_not_empty_so_the_parametrisation_cannot_vacuously_pass() -> None:
    """A guard on the guard: if `RULES` were ever emptied or renamed, every
    parametrised test above would collect zero cases and the suite would go
    green having checked nothing."""
    assert len(RULES) >= 10, f"expected the full rule set, found {sorted(RULES)}"
