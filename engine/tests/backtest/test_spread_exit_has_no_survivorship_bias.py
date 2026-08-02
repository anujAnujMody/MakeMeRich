"""A spread whose hold window runs past the close must still RESOLVE.

Found on 2026-08-02, and it is the most dangerous bug this project has hit.

`_walk_spread` computed `horizon_end = entry + max_hold` and then looked for
an exit price within 10 minutes of that timestamp. With a hold long enough to
reach the close (375 minutes) and a mid-morning entry, `horizon_end` lands
AFTER 15:30 — where no bar exists. The lookup returned `None`, so the whole
trade returned `None`, so it was counted "unresolved" and thrown away.

The trades that survived were therefore only the ones that hit their profit
target DURING the session. Every loser fell through to the dead timestamp and
was silently discarded. The reported result was a **100% win rate with
t=+48** on 562 trades — a number that looks like a discovery and is entirely
an artefact of which trades were allowed to be counted.

Shorter holds were affected too, just less obviously: any entry late enough
that `entry + max_hold` passed the close hit the same dead lookup, dropping
roughly a quarter of trades from every earlier spread run.

The fix exits at the last minute BOTH legs actually traded within the
horizon. That is unbiased (it applies to winners and losers alike) and
genuinely tradeable (a position can always be closed at the last traded
price).
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from pathlib import Path

import pandas as pd
import pytest

from te.backtest.spread_lab import SpreadGeometry, _walk_spread
from te.data.barstore import BarStore
from te.data.charges_loader import load_charge_rate_table
from te.domain.clock import IST
from te.domain.costs import CostModel

_DAY = dt.date(2026, 3, 10)
#: Entry mid-morning, so a long hold necessarily overruns the 15:30 close.
_ENTRY = dt.datetime.combine(_DAY, dt.time(10, 0), tzinfo=IST)

_SHORT = "NIFTY10MAR26P24500"
_LONG = "NIFTY10MAR26P24350"


@pytest.fixture
def rates() -> CostModel:
    return CostModel(load_charge_rate_table(Path(__file__).resolve().parents[2] / "config" / "charges.yaml"))


def _store(tmp_path, *, short_path: list[float], long_path: list[float]) -> BarStore:  # noqa: ANN001
    """Both legs quoted every minute from 09:15 until the 15:30 close."""
    store = BarStore(tmp_path / "bars")
    rows = []
    for symbol, path in ((_SHORT, short_path), (_LONG, long_path)):
        for minute, premium in enumerate(path):
            ts = dt.datetime.combine(_DAY, dt.time(9, 15), tzinfo=IST) + dt.timedelta(minutes=minute)
            rows.append(
                {
                    "symbol": symbol,
                    "exchange": "NFO",
                    "event_ts": ts.astimezone(dt.UTC),
                    "interval": "1m",
                    "o": premium,
                    "h": premium,
                    "l": premium,
                    "c": premium,
                    "v": 0.0,
                    "oi": 0,
                    "ingested_at": ts.astimezone(dt.UTC),
                    "source": "test",
                }
            )
    store.append(pd.DataFrame(rows))
    return store


def _geometry(hold_minutes: int, *, stop_multiple: str = "10.0", target: str = "0.25") -> SpreadGeometry:
    return SpreadGeometry(
        short_otm=0,
        width_strikes=5,
        profit_target_pct=Decimal(target),
        stop_loss_multiple=Decimal(stop_multiple),
        max_hold=dt.timedelta(minutes=hold_minutes),
    )


#: 375 one-minute bars = a full 09:15-15:30 session.
_SESSION_MINUTES = 375


def test_a_losing_spread_held_past_the_close_still_resolves(tmp_path, rates: CostModel) -> None:  # noqa: ANN001
    """The exact regression. The spread moves AGAINST the position all day,
    so it never hits the profit target — under the old code it fell through
    to a dead timestamp and was silently discarded, which is precisely how
    the win rate reached 100%."""
    # Short leg gets more expensive (bad for a seller); long leg lags.
    short_path = [50.0 + m * 0.2 for m in range(_SESSION_MINUTES)]
    long_path = [20.0 + m * 0.05 for m in range(_SESSION_MINUTES)]
    store = _store(tmp_path, short_path=short_path, long_path=long_path)

    walked = _walk_spread(
        store=store,
        short_symbol=_SHORT,
        long_symbol=_LONG,
        entry_ts=_ENTRY,
        geometry=_geometry(375),  # overruns the close by design
    )
    assert walked is not None, (
        "a losing spread held past the close was dropped as 'unresolved' — "
        "this is the survivorship bias that produced a 100% win rate"
    )
    _credit, _se, _le, reason, exit_ts, short_exit, long_exit = walked
    assert reason == "time"
    # Must have exited at the LAST traded minute, not at entry+375m.
    assert exit_ts.astimezone(IST).time() <= dt.time(15, 30)
    # And it must be recorded as the loss it actually is.
    assert int(short_exit) - int(long_exit) > 0


def test_winners_and_losers_resolve_at_the_same_rate(tmp_path, rates: CostModel) -> None:  # noqa: ANN001
    """The property that makes the statistic trustworthy: whether a trade
    resolves must not depend on whether it made money. A bias that keeps
    winners and drops losers is invisible in every summary number."""
    winner_store = _store(
        tmp_path / "win",
        short_path=[50.0 - m * 0.05 for m in range(_SESSION_MINUTES)],
        long_path=[20.0 - m * 0.02 for m in range(_SESSION_MINUTES)],
    )
    loser_store = _store(
        tmp_path / "lose",
        short_path=[50.0 + m * 0.2 for m in range(_SESSION_MINUTES)],
        long_path=[20.0 + m * 0.05 for m in range(_SESSION_MINUTES)],
    )
    # A profit target of 0.95 is nearly unreachable, so the winner also has
    # to survive on the time-exit path rather than escaping via the target.
    geometry = _geometry(375, target="0.95")

    won = _walk_spread(
        store=winner_store, short_symbol=_SHORT, long_symbol=_LONG, entry_ts=_ENTRY,
        geometry=geometry,
    )
    lost = _walk_spread(
        store=loser_store, short_symbol=_SHORT, long_symbol=_LONG, entry_ts=_ENTRY,
        geometry=geometry,
    )
    assert (won is None) == (lost is None), "resolution must not depend on the trade's outcome"
    assert won is not None and lost is not None


@pytest.mark.parametrize("hold_minutes", [30, 60, 180, 375, 600])
def test_every_hold_length_resolves(tmp_path, rates: CostModel, hold_minutes: int) -> None:  # noqa: ANN001
    """Shorter holds were affected by the same dead-lookup whenever the
    entry was late enough to push `entry + max_hold` past the close, which
    is why roughly a quarter of trades vanished from earlier runs. Every
    hold length must resolve from a mid-morning entry."""
    store = _store(
        tmp_path / f"h{hold_minutes}",
        short_path=[50.0 + m * 0.2 for m in range(_SESSION_MINUTES)],
        long_path=[20.0 + m * 0.05 for m in range(_SESSION_MINUTES)],
    )
    walked = _walk_spread(
        store=store, short_symbol=_SHORT, long_symbol=_LONG, entry_ts=_ENTRY,
        geometry=_geometry(hold_minutes),
    )
    assert walked is not None, f"a {hold_minutes}-minute hold failed to resolve"


def test_a_genuine_target_hit_is_still_reported_as_a_target(tmp_path, rates: CostModel) -> None:  # noqa: ANN001
    """The fix must not turn real winners into time exits — the profit
    target still has to fire when it is genuinely reached."""
    # Entry is at minute 45 (10:00). The short leg must stay ABOVE the long
    # leg throughout, or the position is not a credit spread at all and
    # `_walk_spread` correctly refuses it — which is what an earlier version
    # of this fixture accidentally built.
    #
    # Credit at entry is 50 - 20 = 30, so a 0.25 profit target closes once
    # the cost to close falls to 22.5, i.e. once the short leg reaches 42.5.
    short_path = [50.0 if m <= 45 else max(21.0, 50.0 - (m - 45)) for m in range(_SESSION_MINUTES)]
    store = _store(
        tmp_path / "target",
        short_path=short_path,
        long_path=[20.0] * _SESSION_MINUTES,
    )
    walked = _walk_spread(
        store=store, short_symbol=_SHORT, long_symbol=_LONG, entry_ts=_ENTRY,
        geometry=_geometry(375),
    )
    assert walked is not None
    assert walked[3] == "target"


def test_no_shared_bars_after_entry_is_still_refused(tmp_path, rates: CostModel) -> None:  # noqa: ANN001
    """`None` must remain reachable for the genuinely unusable case — a
    contract that never traded again after entry. Removing the bias must not
    mean inventing an exit price where none exists."""
    store = _store(
        tmp_path / "none",
        # Both legs stop quoting before the entry time (10:00 = minute 45).
        short_path=[50.0] * 40,
        long_path=[20.0] * 40,
    )
    walked = _walk_spread(
        store=store, short_symbol=_SHORT, long_symbol=_LONG, entry_ts=_ENTRY,
        geometry=_geometry(375),
    )
    assert walked is None
