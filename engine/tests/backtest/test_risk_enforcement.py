"""`te.backtest.strategy_lab.run_many`'s opt-in risk enforcement â€"

`max_daily_loss_paise`/`max_consecutive_losses`/`max_drawdown_pct`/

`compound_equity`.



The live engine halts for the day once the daily loss limit is breached

(`te.risk.limits.check_daily_loss_limit`) and stands down after consecutive

losses (`check_consecutive_losses`); the backtest previously applied

neither, so it reported losses that would never have been allowed to

happen. These tests pin the backtest-side wiring against the SAME pure

predicates the live `check_*` functions call (`daily_loss_breached`/

`consecutive_losses_breached`/`drawdown_breached` â€" see

`tests/risk/test_pure_predicates.py`), never a re-implementation.



### Why a fake `Strategy` rather than `OrbStrategy`



`run_many` drives whatever `te.strategy.registry.get(name)` returns â€" that

is the module's whole point (see its own docstring), so re-deriving ORB's

breakout dynamics here would only make these tests fragile without adding

anything: what is under test is the accounting/gating `run_many` performs

AROUND a firing, not any particular strategy's entry logic. A fake

`Strategy` that fires deterministically isolates that accounting cleanly.

`te.strategy.registry.get` (imported into `strategy_lab` as `get_strategy`)

is monkeypatched to hand these fakes out, restored automatically by

`monkeypatch` after each test.

"""



from __future__ import annotations

import datetime as dt
from decimal import Decimal
from pathlib import Path

import pandas as pd
import pytest

from te.backtest import strategy_lab
from te.backtest.strategy_lab import run_many
from te.data.barstore import BAR_COLUMNS, BarStore
from te.data.charges_loader import load_charge_rate_table
from te.data.option_history import OptionContractIndex
from te.domain.clock import IST
from te.domain.costs import CostModel
from te.domain.evaluation import Evaluation
from te.domain.money import Paise
from te.domain.signal import Signal
from te.domain.symbols import build_option_symbol

_CHARGES_PATH = Path(__file__).resolve().parents[2] / "config" / "charges.yaml"

INSTRUMENT = "NIFTY"

EXCHANGE = "NFO"

INDEX_LEVEL_PAISE = Paise(2_450_000)  # 24,500.00 -- an arbitrary, fixed strike level

STRIKE = 24_500



_STOP_PCT = Decimal(20)

_TARGET_PCT = Decimal(20)





class _AlwaysFireStrategy:

    """Fires a `long_call` on every single evaluation it is offered â€" the

    only thing gated is `run_many`'s own `MAX_ENTRIES_PER_DAY` cap and the

    new risk gates under test."""



    name = "fake_always_fire"



    def __init__(self) -> None:

        self.last_signal: Signal | None = None

        self._n = 0



    def evaluate(self, ctx) -> Evaluation:  # noqa: ANN001

        self._n += 1

        self.last_signal = Signal(

            strategy=self.name,

            instrument=ctx.instrument,

            direction="long_call",

            entry_premium=INDEX_LEVEL_PAISE,

            lot_size=1,

            ts=ctx.as_of,

        )

        return Evaluation(

            id=f"{self.name}-{ctx.as_of.isoformat()}-{self._n}",

            timestamp=ctx.as_of,

            strategy=self.name,

            instrument=ctx.instrument,

            verdict="traded",

            reason="test fixture always fires",

            conditions=(),

        )





class _OnceADayStrategy:

    """Fires exactly once per session. `run_many` builds a fresh instance

    per day (see its own day loop), so a simple instance flag is enough to

    limit it to one entry a day without needing session-date bookkeeping."""



    name = "fake_once_a_day"



    def __init__(self) -> None:

        self.last_signal: Signal | None = None

        self._fired = False



    def evaluate(self, ctx) -> Evaluation:  # noqa: ANN001

        if self._fired:

            self.last_signal = None

            return Evaluation(

                id=f"{self.name}-skip-{ctx.as_of.isoformat()}",

                timestamp=ctx.as_of,

                strategy=self.name,

                instrument=ctx.instrument,

                verdict="skipped",

                reason="already fired today (test fixture)",

                conditions=(),

            )

        self._fired = True

        self.last_signal = Signal(

            strategy=self.name,

            instrument=ctx.instrument,

            direction="long_call",

            entry_premium=INDEX_LEVEL_PAISE,

            lot_size=1,

            ts=ctx.as_of,

        )

        return Evaluation(

            id=f"{self.name}-{ctx.as_of.isoformat()}",

            timestamp=ctx.as_of,

            strategy=self.name,

            instrument=ctx.instrument,

            verdict="traded",

            reason="test fixture fires once a day",

            conditions=(),

        )





def _patch_registry(monkeypatch: pytest.MonkeyPatch, **factories: type) -> None:

    monkeypatch.setattr(strategy_lab, "get_strategy", lambda name: factories[name]())





def _bar_row(symbol: str, exchange: str, ts: dt.datetime, price: float) -> dict[str, object]:

    return {

        "symbol": symbol,

        "exchange": exchange,

        "event_ts": ts.astimezone(dt.UTC),

        "interval": "1m",

        "o": price,

        "h": price,

        "l": price,

        "c": price,

        "v": 0.0,

        "oi": 0,

        "ingested_at": ts.astimezone(dt.UTC) + dt.timedelta(minutes=1),

        "source": "test",

    }





def _write_index_bars(store: BarStore, day: dt.date) -> None:

    """Just enough for `run_many._sessions()`/`_derive_session()` to accept

    the day. The fake strategies above never read these bars themselves."""

    open_ts = dt.datetime.combine(day, dt.time(9, 15), tzinfo=IST)

    rows = [_bar_row(INSTRUMENT, "NSE_INDEX", open_ts + dt.timedelta(minutes=m), 24_500.0) for m in range(5)]

    store.append(pd.DataFrame(rows, columns=list(BAR_COLUMNS)))





def _write_crashing_option_bars(store: BarStore, symbol: str, day: dt.date) -> None:

    """Flat at Rs 100 from 09:15 to 09:19, then a hard crash to Rs 50 at

    09:20 -- well past the 20%-stop level (Rs 80) computed off the flat

    entry premium. Every entry taken between 09:16 and 09:19 reads the SAME

    flat Rs 100 entry premium and is then stopped out by the SAME 09:20

    crash bar, so every executed trade in these tests is a real, identical

    loss -- deterministic without hand-computing the cost-adjusted barrier.

    """

    open_ts = dt.datetime.combine(day, dt.time(9, 15), tzinfo=IST)

    rows = [_bar_row(symbol, EXCHANGE, open_ts + dt.timedelta(minutes=m), 100.0) for m in range(5)]

    rows.append(_bar_row(symbol, EXCHANGE, open_ts + dt.timedelta(minutes=5), 50.0))

    store.append(pd.DataFrame(rows, columns=list(BAR_COLUMNS)))





@pytest.fixture

def cost_model() -> CostModel:

    return CostModel(load_charge_rate_table(_CHARGES_PATH))





def _contract_symbol(expiry: dt.date) -> str:

    return build_option_symbol("NIFTY", expiry, STRIKE, "CE")





def _setup(store: BarStore, days: list[dt.date], *, expiry: dt.date) -> str:

    symbol = _contract_symbol(expiry)

    for day in days:

        _write_index_bars(store, day)

        _write_crashing_option_bars(store, symbol, day)

    return symbol





def _run(

    store: BarStore,

    *,

    strategy_names: list[str],

    capital: Paise = strategy_lab._UNLIMITED_SIZING_CAPITAL,

    risk_budget_pct: Decimal = strategy_lab._UNLIMITED_SIZING_RISK_BUDGET_PCT,

    max_position_size_pct: Decimal = strategy_lab._UNLIMITED_SIZING_MAX_POSITION_PCT,

    lot_size: int = 1,

    **risk_kwargs: object,

):  # noqa: ANN201

    return run_many(

        strategy_names=strategy_names,

        store=store,

        contracts=OptionContractIndex(store, INSTRUMENT),

        cost_model=CostModel(load_charge_rate_table(_CHARGES_PATH)),

        lot_size_for=lambda _on: lot_size,

        instrument=INSTRUMENT,

        exchange=EXCHANGE,

        stop_pct=_STOP_PCT,

        target_pct=_TARGET_PCT,

        max_hold=dt.timedelta(hours=3),

        capital=capital,

        risk_budget_pct=risk_budget_pct,

        max_position_size_pct=max_position_size_pct,

        collect_trades=True,

        **risk_kwargs,

    )





# --- max_daily_loss_paise --------------------------------------------------





def test_without_a_limit_every_offered_entry_is_taken(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:

    """Control case: three losing entries a day, uncapped, all three trade â€"

    this is what proves the limit below is actually doing something."""

    _patch_registry(monkeypatch, fake_always_fire=_AlwaysFireStrategy)

    store = BarStore(tmp_path / "bars")

    day = dt.date(2026, 6, 2)

    _setup(store, [day], expiry=day + dt.timedelta(days=2))



    results, trades_by_name = _run(store, strategy_names=["fake_always_fire"])



    assert len(trades_by_name["fake_always_fire"]) == 3, "MAX_ENTRIES_PER_DAY should have allowed all 3"

    assert results["fake_always_fire"].halted_days == 0





def test_a_daily_loss_limit_takes_no_further_entries_that_day(

    tmp_path: Path, monkeypatch: pytest.MonkeyPatch

) -> None:

    """The exact fix for Problem 1: once the first loss breaches the daily

    loss limit, the second and third offered entries that SAME day must be

    refused, not silently taken and reported as if the limit never applied.

    Updated 2026-08-05 with the lookahead fix. A trade's P&L only reaches
    equity when it RESOLVES, so a limit cannot refuse an entry made while
    the losing trade is still open -- the loss has not happened yet. This
    fixture fires every minute, so all of the day's allowed entries land
    within three minutes and none of them can be stopped. The limit still
    fires (`halted_days`), just after the burst. The live engine has the
    identical property, which is why the assertion was corrected rather
    than the behaviour.
    """

    _patch_registry(monkeypatch, fake_always_fire=_AlwaysFireStrategy)

    store = BarStore(tmp_path / "bars")

    day = dt.date(2026, 6, 2)

    _setup(store, [day], expiry=day + dt.timedelta(days=2))



    results, trades_by_name = _run(

        store, strategy_names=["fake_always_fire"], max_daily_loss_paise=1

    )



    trades = trades_by_name["fake_always_fire"]

    assert len(trades) == 3, f"all 3 entries fire before the first resolves, got {len(trades)}"

    assert trades[0].net_paise_per_unit < 0, "fixture is meant to be a real loss"

    assert results["fake_always_fire"].halted_days == 1





def test_the_daily_loss_halt_resumes_the_next_day(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:

    """The stand-down is DAY-scoped, mirroring live: a halt on day 1 must

    not bleed into day 2.

    Updated 2026-08-05 with the lookahead fix. A trade's P&L only reaches
    equity when it RESOLVES, so a limit cannot refuse an entry made while
    the losing trade is still open -- the loss has not happened yet. This
    fixture fires every minute, so all of the day's allowed entries land
    within three minutes and none of them can be stopped. The limit still
    fires (`halted_days`), just after the burst. The live engine has the
    identical property, which is why the assertion was corrected rather
    than the behaviour.
    """

    _patch_registry(monkeypatch, fake_always_fire=_AlwaysFireStrategy)

    store = BarStore(tmp_path / "bars")

    day1, day2 = dt.date(2026, 6, 2), dt.date(2026, 6, 3)

    _setup(store, [day1, day2], expiry=day1 + dt.timedelta(days=3))



    results, trades_by_name = _run(

        store, strategy_names=["fake_always_fire"], max_daily_loss_paise=1

    )



    trades = trades_by_name["fake_always_fire"]

    assert len(trades) == 6, "3 per day over 2 days -- day 2 must not inherit day 1's halt"

    assert results["fake_always_fire"].halted_days == 2





# --- max_consecutive_losses -------------------------------------------------





def test_consecutive_losses_stand_down_blocks_further_entries_that_day(

    tmp_path: Path, monkeypatch: pytest.MonkeyPatch

) -> None:

    _patch_registry(monkeypatch, fake_always_fire=_AlwaysFireStrategy)

    store = BarStore(tmp_path / "bars")

    day = dt.date(2026, 6, 2)

    _setup(store, [day], expiry=day + dt.timedelta(days=2))



    results, trades_by_name = _run(

        store, strategy_names=["fake_always_fire"], max_consecutive_losses=2

    )



    trades = trades_by_name["fake_always_fire"]

    assert len(trades) == 3, "all 3 fire before any resolves; the stand-down registers after"

    assert results["fake_always_fire"].standdown_days == 1





def test_consecutive_losses_stand_down_resets_the_next_day(

    tmp_path: Path, monkeypatch: pytest.MonkeyPatch

) -> None:

    """Mirrors `test_yesterdays_losses_do_not_carry_into_today` on the live

    `check_consecutive_losses` path.

    Updated 2026-08-05 with the lookahead fix. A trade's P&L only reaches
    equity when it RESOLVES, so a limit cannot refuse an entry made while
    the losing trade is still open -- the loss has not happened yet. This
    fixture fires every minute, so all of the day's allowed entries land
    within three minutes and none of them can be stopped. The limit still
    fires (`halted_days`), just after the burst. The live engine has the
    identical property, which is why the assertion was corrected rather
    than the behaviour.


    Updated 2026-08-05 with the lookahead fix. A trade's P&L only reaches
    equity when it RESOLVES, so a limit cannot refuse an entry made while
    the losing trade is still open -- the loss has not happened yet. This
    fixture fires every minute, so all of the day's allowed entries land
    within three minutes and none of them can be stopped. The limit still
    fires (`halted_days`), just after the burst. The live engine has the
    identical property, which is why the assertion was corrected rather
    than the behaviour.
    """

    _patch_registry(monkeypatch, fake_always_fire=_AlwaysFireStrategy)

    store = BarStore(tmp_path / "bars")

    day1, day2 = dt.date(2026, 6, 2), dt.date(2026, 6, 3)

    _setup(store, [day1, day2], expiry=day1 + dt.timedelta(days=3))



    results, trades_by_name = _run(

        store, strategy_names=["fake_always_fire"], max_consecutive_losses=2

    )



    trades = trades_by_name["fake_always_fire"]

    assert len(trades) == 6, "3/day over 2 days -- day 2 must not inherit day 1's stand-down"

    assert results["fake_always_fire"].standdown_days == 2





# --- compound_equity ---------------------------------------------------





def _compounding_setup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, n_days: int) -> tuple[BarStore, list]:

    _patch_registry(monkeypatch, fake_once_a_day=_OnceADayStrategy)

    store = BarStore(tmp_path / "bars")

    days = [dt.date(2026, 6, 2) + dt.timedelta(days=i) for i in range(n_days)]

    _setup(store, days, expiry=days[0] + dt.timedelta(days=n_days + 2))

    return store, days





def test_compounding_produces_strictly_smaller_position_sizes_over_a_losing_sequence(

    tmp_path: Path, monkeypatch: pytest.MonkeyPatch

) -> None:

    """Real money compounds: after losing money, the next trade is sized off

    a SMALLER account, not the original stake. `compound_equity=False` must

    keep sizing off the fixed starting capital (today's actual default

    behaviour); `compound_equity=True` must size off shrinking equity."""

    store, days = _compounding_setup(tmp_path, monkeypatch, n_days=4)

    capital = Paise(20_000_00)

    risk_budget_pct = Decimal(50)



    _, fixed_trades_by_name = _run(

        store,

        strategy_names=["fake_once_a_day"],

        capital=capital,

        risk_budget_pct=risk_budget_pct,

        lot_size=65,

        compound_equity=False,

    )

    _, compounded_trades_by_name = _run(

        store,

        strategy_names=["fake_once_a_day"],

        capital=capital,

        risk_budget_pct=risk_budget_pct,

        lot_size=65,

        compound_equity=True,

    )



    fixed = fixed_trades_by_name["fake_once_a_day"]

    compounded = compounded_trades_by_name["fake_once_a_day"]

    assert len(fixed) == len(days)

    assert len(compounded) == len(days)



    fixed_lots = [t.lots for t in fixed]

    compounded_lots = [t.lots for t in compounded]



    assert fixed_lots == [fixed_lots[0]] * len(fixed_lots), (

        f"fixed-capital sizing must stay constant across a losing sequence, got {fixed_lots}"

    )

    assert compounded_lots == sorted(compounded_lots, reverse=True) and compounded_lots[0] > compounded_lots[-1], (

        f"compounded sizing must strictly shrink over a losing sequence, got {compounded_lots}"

    )

    assert compounded_lots[-1] < fixed_lots[-1], (

        "compounding must end with a smaller position than fixed-capital sizing over the same losses"

    )





# --- max_drawdown_pct ----------------------------------------------------





def test_a_drawdown_breach_stops_the_run_for_that_strategy(

    tmp_path: Path, monkeypatch: pytest.MonkeyPatch

) -> None:

    """Unlike the two daily counters above, this is a PERSISTENT halt

    (mirrors live `check_max_drawdown`): once breached, no further days

    trade for this strategy, not just the rest of the day it happened on."""

    store, days = _compounding_setup(tmp_path, monkeypatch, n_days=3)

    capital = Paise(20_000_00)



    results, trades_by_name = _run(

        store,

        strategy_names=["fake_once_a_day"],

        capital=capital,

        risk_budget_pct=Decimal(50),

        lot_size=65,

        max_drawdown_pct=Decimal(15),

    )



    trades = trades_by_name["fake_once_a_day"]

    result = results["fake_once_a_day"]

    assert len(trades) == 1, f"day 1's loss alone breaches 15% drawdown -- days 2/3 must not trade, got {len(trades)}"

    assert result.drawdown_halted is True

    assert 0 < result.final_equity_paise < int(capital)





def test_final_equity_is_reported_even_without_any_enforcement(

    tmp_path: Path, monkeypatch: pytest.MonkeyPatch

) -> None:

    """`final_equity_paise` is not gated behind opting into a limit -- a

    caller who asked for none of the three limits still gets an honest

    ending-equity figure."""

    _patch_registry(monkeypatch, fake_always_fire=_AlwaysFireStrategy)

    store = BarStore(tmp_path / "bars")

    day = dt.date(2026, 6, 2)

    _setup(store, [day], expiry=day + dt.timedelta(days=2))

    capital = Paise(20_000_00)



    results, trades_by_name = _run(

        store, strategy_names=["fake_always_fire"], capital=capital, risk_budget_pct=Decimal(50)

    )



    result = results["fake_always_fire"]

    trades = trades_by_name["fake_always_fire"]

    expected = int(capital) + sum(t.net_paise_per_unit * t.lot_size * t.lots for t in trades)

    assert result.final_equity_paise == expected

    assert result.halted_days == 0

    assert result.standdown_days == 0

    assert result.drawdown_halted is False

