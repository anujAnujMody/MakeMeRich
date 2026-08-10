"""`te.backtest.engine.run_backtest` — bar-close event loop, zero lookahead
by construction (delegates every read to `bars_asof`, adds no window logic
of its own — see `tests/data/test_asof.py` for the underlying point-in-time
guarantee this loop inherits)."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pandas as pd
import pytest

from te.backtest.engine import BacktestConfig, run_backtest
from te.backtest.fills import BacktestFillEngine
from te.broker.protocol import FillReport
from te.data.barstore import BAR_COLUMNS, BarStore
from te.domain.clock import IST
from te.domain.costs import ChargeRates, CostModel
from te.domain.geometry import AbsolutePointGeometry
from te.domain.money import Paise
from te.domain.orders import OrderIntent
from te.domain.pnl import net_pnl
from te.strategy.orb import OrbParams, OrbStrategy

#: `run_backtest` has no separate option-premium bar series yet (see
#: `_last_close_premium`'s docstring in `te/backtest/engine.py`) -- it reads
#: `INSTRUMENT`'s own recorded bars and treats the close, in index POINTS,
#: as if it were a premium in RUPEES (`Paise(int(round(close * 100)))`).
#: Every bar this file writes is therefore an INDEX bar being fed in as a
#: stand-in for an option premium, even though the symbol/exchange below
#: read as an option ("NIFTY"/"NFO"). The fixtures below deliberately use
#: bars at 100-112 points, which happens to produce a plausible-looking
#: Rs 100-112 premium; a REAL NIFTY index level (~24,500) run through the
#: same conversion would price every entry at ~Rs 24,500 -- the substitution
#: is invisible unless a reader already knows to look for it. The mechanics
#: (`bar close x 100`) are pinned by the tests below; this comment is what
#: makes the MEANING (an index level standing in for a premium) visible.
#: `te.backtest.strategy_lab`/`te.backtest.sweep` are the real
#: option-premium path (`te.ml.labeling.label_one_firing_on_premium`,
#: covered by `tests/backtest/test_straddle_costs.py` and friends) -- this
#: module (`run_backtest`) is Phase 1 scope and stays this way deliberately;
#: see `te/backtest/engine.py`'s module docstring.
INSTRUMENT = "NIFTY"
EXCHANGE = "NFO"


class _RecordingFillEngine(BacktestFillEngine):
    """Wraps `BacktestFillEngine` to capture every `OrderIntent` it fills,
    so a test can assert on the exact order the engine placed (quantity,
    side) rather than only on the resulting `ClosedTrade`, whose cost
    assertions are otherwise self-consistent for ANY lot count (see
    `te/backtest/engine.py:224-234`'s sizing/`OrderIntent` construction)."""

    def __init__(self, *, cost_model: CostModel, slippage_bps: Decimal = Decimal(0)) -> None:
        super().__init__(cost_model=cost_model, slippage_bps=slippage_bps)
        self.intents: list[OrderIntent] = []

    def fill(self, intent: OrderIntent) -> FillReport:
        self.intents.append(intent)
        return super().fill(intent)


@pytest.fixture
def rates() -> ChargeRates:
    return ChargeRates(
        effective_from=dt.date(2026, 4, 1),
        verified_at=dt.date(2026, 7, 29),
        brokerage_per_executed_order_paise=Paise(2000),
        stt_sell_bps=Decimal("15.0"),
        stt_exercise_intrinsic_bps=Decimal("15.0"),
        exchange_txn_bps={"NFO": Decimal("3.553"), "BFO": Decimal("3.25")},
        sebi_bps=Decimal("0.01"),
        gst_pct=Decimal("18.0"),
        stamp_buy_bps=Decimal("0.3"),
    )


@pytest.fixture
def cost_model(rates: ChargeRates) -> CostModel:
    return CostModel(rates)


def _bar(event_ts: dt.datetime, *, o: float, h: float, low: float, c: float, v: int) -> dict[str, object]:
    return {
        "symbol": INSTRUMENT, "exchange": EXCHANGE, "event_ts": event_ts, "interval": "1m",
        "o": o, "h": h, "l": low, "c": c, "v": v, "oi": 0, "ingested_at": event_ts, "source": "test",
    }


def _open(minute: int) -> dt.datetime:
    base = dt.datetime(2026, 7, 29, 9, 15, tzinfo=IST)
    return base + dt.timedelta(minutes=minute)


@pytest.fixture
def store(tmp_path) -> BarStore:  # noqa: ANN001
    return BarStore(tmp_path)


def _config() -> BacktestConfig:
    return BacktestConfig(
        capital=Paise(30_000_00),
        risk_budget_pct=Decimal("2"),
        min_edge_multiple=Decimal("1"),
        exit_geometry=AbsolutePointGeometry(
            stop_distance=Paise(300), target_distance=Paise(200), trailing_distance=None
        ),
        max_hold=dt.timedelta(hours=6),
        hard_exit_by=dt.time(15, 20),
        lot_size=65,
    )


def test_backtest_enters_on_breakout_and_exits_on_target(store: BarStore, cost_model: CostModel) -> None:
    rows = [
        _bar(_open(0), o=100, h=105, low=95, c=100, v=1_000),
        _bar(_open(1), o=100, h=101, low=99, c=100.5, v=1_000),
        _bar(_open(2), o=100, h=101, low=99, c=100.2, v=1_000),
        _bar(_open(15), o=100, h=110, low=100, c=108, v=1_500),  # breakout, entry premium 108.00 -> 10800p
        _bar(_open(16), o=108, h=112, low=107, c=112, v=1_200),  # premium 11200p, hits target (11000p)
    ]
    store.append(pd.DataFrame(rows, columns=list(BAR_COLUMNS)))

    strategy = OrbStrategy(OrbParams(opening_range_minutes=15, min_opening_bars=3))
    # Slippage explicitly disabled: this test pins the exact entry/exit
    # premium MECHANICS (bar close -> fill price -> target barrier), so it
    # must not also absorb `BacktestFillEngine`'s default slippage floor.
    # `tests/backtest/test_fills.py` covers that default separately.
    fills = _RecordingFillEngine(cost_model=cost_model, slippage_bps=Decimal(0))
    timestamps = [_open(i) for i in [1, 2, 16, 17]]

    result = run_backtest(
        store=store, instrument=INSTRUMENT, exchange=EXCHANGE, strategy=strategy, cost_model=cost_model,
        fills=fills, config=_config(), timestamps=timestamps,
    )

    assert len(result.trades) == 1
    trade = result.trades[0]
    assert trade.exit_reason == "target"
    assert trade.entry_premium == Paise(10_800)
    assert trade.exit_premium == Paise(11_200)

    # Exact lot count, hand-derived from `_config()`'s capital/risk budget
    # rather than recomputed from the trade — the cost/net assertions below
    # recompute their expected figures from `trade.lots`, which is
    # self-consistent for ANY lot count and would not catch a sizing bug.
    #
    # capital=Rs 30,000 (3,000,000p), risk_budget_pct=2% -> risk budget
    # 60,000p. risk_per_lot = (entry - stop) * lot_size = 300p * 65 =
    # 19,500p. lots_by_risk = 60,000 // 19,500 = 3.
    # lot_cost = entry * lot_size = 10,800p * 65 = 702,000p.
    # lots_by_capital = 3,000,000 // 702,000 = 4.
    # lots = min(3, 4, 4) = 3.
    assert trade.lots == 3

    entry_intent = fills.intents[0]
    assert entry_intent.side == "BUY"
    assert entry_intent.quantity == trade.lot_size * trade.lots == 65 * 3

    qty = trade.lots * trade.lot_size
    expected_costs = cost_model.round_trip(
        entry_premium=trade.entry_premium, exit_premium=trade.exit_premium, qty=qty, exchange=EXCHANGE,
        on=trade.exit_ts.date(),
    )
    expected_net = net_pnl(trade.entry_premium, trade.exit_premium, qty, expected_costs)

    assert trade.costs == expected_costs
    assert trade.net_pnl == expected_net
    # Net must differ from gross by exactly the real cost total — never a
    # gross figure masquerading as net.
    assert int(trade.net_pnl) == int(trade.gross_pnl) - expected_costs.total


def test_backtest_only_ever_sees_bars_closed_at_or_before_as_of(store: BarStore, cost_model: CostModel) -> None:
    """Zero-lookahead sanity check: bars recorded FAR in the future (beyond
    every `as_of` the loop steps through) must never influence the result —
    if they did, the strategy would fire on the very first timestamp
    instead of skipping until the opening range/breakout bars are visible."""
    rows = [
        _bar(_open(0), o=100, h=101, low=99, c=100, v=1_000),
        _bar(_open(1), o=100, h=101, low=99, c=100.2, v=1_000),
        _bar(_open(2), o=100, h=101, low=99, c=100.1, v=1_000),
        # A dramatic future breakout bar the loop's early timestamps must
        # never be able to see.
        _bar(_open(15), o=100, h=200, low=100, c=190, v=50_000),
    ]
    store.append(pd.DataFrame(rows, columns=list(BAR_COLUMNS)))

    strategy = OrbStrategy(OrbParams(opening_range_minutes=15, min_opening_bars=3))
    fills = BacktestFillEngine(cost_model=cost_model)

    # Step through only the FIRST two timestamps — the breakout bar at
    # minute 15 must not be visible yet (its close_ts is in the future
    # relative to as_of=_open(1)/_open(2)).
    result = run_backtest(
        store=store, instrument=INSTRUMENT, exchange=EXCHANGE, strategy=strategy, cost_model=cost_model,
        fills=fills, config=_config(), timestamps=[_open(1), _open(2)],
    )

    assert result.trades == ()


def test_backtest_rejects_zero_lot_sizing_silently_as_a_skip(store: BarStore, cost_model: CostModel) -> None:
    """A signal that fires but can't be sized (e.g. capital too small)
    produces no trade, not a crash — mirrors `te.risk.sizing`'s always-
    explained-skip guarantee, just without a persisted `SkippedSignal` row
    (backtest has no DB session in this phase)."""
    rows = [
        _bar(_open(0), o=100, h=105, low=95, c=100, v=1_000),
        _bar(_open(1), o=100, h=101, low=99, c=100.5, v=1_000),
        _bar(_open(2), o=100, h=101, low=99, c=100.2, v=1_000),
        _bar(_open(15), o=100, h=110, low=100, c=108, v=1_500),
    ]
    store.append(pd.DataFrame(rows, columns=list(BAR_COLUMNS)))

    strategy = OrbStrategy(OrbParams(opening_range_minutes=15, min_opening_bars=3))
    fills = BacktestFillEngine(cost_model=cost_model)
    tiny_capital_config = BacktestConfig(
        capital=Paise(100),  # cannot afford even 1 lot at premium 108.00 x 65
        risk_budget_pct=Decimal("2"), min_edge_multiple=Decimal("1"),
        exit_geometry=AbsolutePointGeometry(
            stop_distance=Paise(300), target_distance=Paise(200), trailing_distance=None
        ),
        max_hold=dt.timedelta(hours=6), hard_exit_by=dt.time(15, 20), lot_size=65,
    )

    result = run_backtest(
        store=store, instrument=INSTRUMENT, exchange=EXCHANGE, strategy=strategy, cost_model=cost_model,
        fills=fills, config=tiny_capital_config, timestamps=[_open(1), _open(2), _open(16)],
    )

    assert result.trades == ()


def test_backtest_prices_entries_off_the_index_level_not_a_real_option_premium(
    store: BarStore, cost_model: CostModel
) -> None:
    """Pins the MEANING of `_last_close_premium`'s conversion, not just its
    arithmetic: at a REALISTIC NIFTY index level (~24,500 points, not the
    100-112 range every other fixture in this file uses, which happens to
    look like a plausible premium), the resulting `entry_premium` is
    Rs 24,500-ish -- an unmistakably wrong price for an option premium,
    which is the whole point. `run_backtest` has no separate option-premium
    bar series in this phase (see `INSTRUMENT`'s module comment above); it
    prices every entry directly off the underlying's own recorded bars."""
    rows = [
        _bar(_open(0), o=24_500, h=24_550, low=24_450, c=24_500, v=1_000),
        _bar(_open(1), o=24_500, h=24_520, low=24_480, c=24_505, v=1_000),
        _bar(_open(2), o=24_500, h=24_520, low=24_480, c=24_502, v=1_000),
        # Breakout: closes clearly beyond the opening range.
        _bar(_open(15), o=24_500, h=25_000, low=24_500, c=24_900, v=1_500),
        # Closes well above the 200-point target, so the position resolves
        # on the very next step -- otherwise a still-open position never
        # becomes a `ClosedTrade` and there is nothing to read the priced
        # `entry_premium` off.
        _bar(_open(16), o=24_900, h=24_950, low=24_900, c=24_920, v=1_200),
    ]
    store.append(pd.DataFrame(rows, columns=list(BAR_COLUMNS)))

    strategy = OrbStrategy(OrbParams(opening_range_minutes=15, min_opening_bars=3))
    fills = BacktestFillEngine(cost_model=cost_model, slippage_bps=Decimal(0))
    config = BacktestConfig(
        # Capital deliberately huge, not the usual Rs 30,000 fixture value:
        # at an index-level "premium" of ~Rs 24,900, one lot alone costs
        # ~Rs 16.2 lakh (24,900 x 65), which a real account could never
        # afford -- itself a symptom of the same substitution this test
        # exists to make visible. A realistic capital would size to zero
        # lots here and there would be no `ClosedTrade` to read the price
        # off; that is not what this test is about, so it is sidestepped.
        capital=Paise(10**12), risk_budget_pct=Decimal("2"), min_edge_multiple=Decimal("0"),
        exit_geometry=AbsolutePointGeometry(
            stop_distance=Paise(300), target_distance=Paise(200), trailing_distance=None
        ),
        max_hold=dt.timedelta(hours=6), hard_exit_by=dt.time(15, 20), lot_size=65,
    )

    result = run_backtest(
        store=store, instrument=INSTRUMENT, exchange=EXCHANGE, strategy=strategy, cost_model=cost_model,
        fills=fills, config=config, timestamps=[_open(1), _open(2), _open(16), _open(17)],
    )

    # 24,900.00 -> 2,490,000p, the underlying's INDEX level, not any real
    # NIFTY option premium (which, at the same underlying level, trades for
    # a small fraction of that -- typically Rs 50-500 for a near-the-money
    # weekly contract).
    assert len(result.trades) == 1
    assert int(result.trades[0].entry_premium) == 24_900 * 100


def test_backtest_refuses_an_entry_too_close_to_the_hard_exit(store: BarStore, cost_model: CostModel) -> None:
    """Mirrors `te.engine.cycle.run_once`'s `min_minutes_before_hard_exit`
    gate (`te/engine/cycle.py:436-447`) exactly: a firing too close to the
    hard exit carries full downside against upside that is unreachable by
    construction. `hard_exit_by=15:15` and `min_minutes_before_hard_exit=40`
    put the cutoff at 14:35 -- a decision made at 14:51 (well past it) must
    be refused, even though the breakout bar itself is genuine and would
    otherwise trade (see `test_backtest_enters_on_breakout_and_exits_on_target`
    for the same bars/geometry producing a real trade without this gate)."""
    rows = [
        _bar(_open(0), o=100, h=105, low=95, c=100, v=1_000),
        _bar(_open(1), o=100, h=101, low=99, c=100.5, v=1_000),
        _bar(_open(2), o=100, h=101, low=99, c=100.2, v=1_000),
        # 14:50 IST breakout -- a genuine crossing, visible one minute later.
        # If the gate did not refuse it, minute 336 (target 11000p) would
        # hit `target_distance=200` and close the trade one step later --
        # exactly `test_backtest_enters_on_breakout_and_exits_on_target`'s
        # bars, shifted 320 minutes later in the day, so an entry here would
        # be indistinguishable from a genuine, otherwise-tradeable signal.
        _bar(_open(335), o=100, h=110, low=100, c=108, v=1_500),
        _bar(_open(336), o=108, h=112, low=107, c=112, v=1_200),
    ]
    store.append(pd.DataFrame(rows, columns=list(BAR_COLUMNS)))

    strategy = OrbStrategy(OrbParams(opening_range_minutes=15, min_opening_bars=3))
    fills = BacktestFillEngine(cost_model=cost_model, slippage_bps=Decimal(0))
    config = BacktestConfig(
        capital=Paise(30_000_00), risk_budget_pct=Decimal("2"), min_edge_multiple=Decimal("1"),
        exit_geometry=AbsolutePointGeometry(
            stop_distance=Paise(300), target_distance=Paise(200), trailing_distance=None
        ),
        max_hold=dt.timedelta(hours=6), hard_exit_by=dt.time(15, 15), lot_size=65,
        min_minutes_before_hard_exit=40,
    )

    # as_of=14:51 IST -- one minute after the breakout bar closes, and past
    # the 14:35 cutoff (15:15 hard exit minus 40 minutes). A second step at
    # 14:52 gives an unrefused entry room to reach its target, so the
    # assertion below actually distinguishes "refused" from "opened but
    # never got a chance to close".
    result = run_backtest(
        store=store, instrument=INSTRUMENT, exchange=EXCHANGE, strategy=strategy, cost_model=cost_model,
        fills=fills, config=config, timestamps=[_open(336), _open(337)],
    )

    assert result.trades == ()


def test_backtest_caps_entries_per_underlying_per_day(store: BarStore, cost_model: CostModel) -> None:
    """Mirrors `te.engine.cycle.run_once`'s max-entries-per-underlying-per-day
    guard (`te/engine/cycle.py:529-539`). `BacktestConfig`'s default is 2
    (see its docstring), so a day offering THREE genuine breakout crossings
    must still produce only 2 trades. Bar timings account for `bars_asof`'s
    `close_ts = event_ts + interval`: a bar closing at minute N is first
    visible at `as_of=N+1`."""
    rows = [
        _bar(_open(0), o=100, h=105, low=95, c=100, v=1_000),
        _bar(_open(1), o=100, h=101, low=99, c=100.5, v=1_000),
        _bar(_open(2), o=100, h=101, low=99, c=100.2, v=1_000),
        _bar(_open(15), o=100, h=110, low=100, c=108, v=1_500),  # breakout 1, visible at as_of=16
        _bar(_open(16), o=108, h=112, low=107, c=112, v=1_200),  # target hit, visible at as_of=17
        _bar(_open(17), o=112, h=112, low=100, c=100, v=1_000),  # back inside the opening range
        _bar(_open(18), o=100, h=110, low=100, c=108, v=1_500),  # breakout 2, visible at as_of=19
        _bar(_open(19), o=108, h=112, low=107, c=112, v=1_200),  # target hit, visible at as_of=20
        _bar(_open(20), o=112, h=112, low=100, c=100, v=1_000),  # back inside the opening range
        _bar(_open(21), o=100, h=110, low=100, c=108, v=1_500),  # breakout 3 -- must be refused
        _bar(_open(22), o=108, h=112, low=107, c=112, v=1_200),
    ]
    store.append(pd.DataFrame(rows, columns=list(BAR_COLUMNS)))

    strategy = OrbStrategy(OrbParams(opening_range_minutes=15, min_opening_bars=3))
    fills = BacktestFillEngine(cost_model=cost_model, slippage_bps=Decimal(0))
    timestamps = [_open(i) for i in [16, 17, 18, 19, 20, 21, 22, 23]]

    result = run_backtest(
        store=store, instrument=INSTRUMENT, exchange=EXCHANGE, strategy=strategy, cost_model=cost_model,
        fills=fills, config=_config(), timestamps=timestamps,
    )

    assert len(result.trades) == 2, "the third genuine breakout must be refused by the per-day entry cap"
    assert [t.entry_premium for t in result.trades] == [Paise(10_800), Paise(10_800)]
