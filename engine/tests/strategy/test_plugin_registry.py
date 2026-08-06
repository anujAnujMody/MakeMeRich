"""The plug-in system's own guarantees.

Three failure modes are pinned here, and each is one that would otherwise be
silent:

1. **A strategy that never registers.** Auto-discovery by decorator fails
   quietly when nothing imports the module; on a page of thirty rules, one
   missing is undetectable by eye. Every rule module must contribute.
2. **A strategy that reads the future.** The base class fetches bars through
   `bars_asof`, but nothing stops a subclass reaching for `ctx.store`
   directly. Adding later bars to the store must not change any decision.
3. **A spec that cannot be rendered or searched.** An unbounded numeric
   parameter is an unbounded search space and an un-renderable UI control.
"""

from __future__ import annotations

import datetime as dt

import pandas as pd
import pytest

from te.data.barstore import BarStore
from te.domain.clock import IST
from te.strategy import rules
from te.strategy.registry import available, get, spec
from te.strategy.session_rule import SessionRule
from te.strategy.spec import StrategySpec

_DAY = dt.date(2026, 3, 10)
_PREVIOUS = dt.date(2026, 3, 9)


def _bar(symbol: str, ts: dt.datetime, o: float, h: float, low: float, c: float) -> dict[str, object]:
    return {
        "symbol": symbol,
        "exchange": "NSE_INDEX",
        "event_ts": ts.astimezone(dt.UTC),
        "interval": "1m",
        "o": o,
        "h": h,
        "l": low,
        "c": c,
        "v": 0.0,
        "oi": 0,
        "ingested_at": ts.astimezone(dt.UTC),
        "source": "test",
    }


def _session_bars(day: dt.date, start_price: float, drift: float, count: int) -> list[dict[str, object]]:
    """A session with a flat first hour then a drift, so range, trend and
    reversal rules all have something to act on."""
    bars = []
    price = start_price
    for minute in range(count):
        ts = dt.datetime.combine(day, dt.time(9, 15), tzinfo=IST) + dt.timedelta(minutes=minute)
        step = 0.0 if minute < 60 else drift
        wobble = 0.4 if minute % 7 == 0 else -0.3
        price += step + wobble
        bars.append(_bar("NIFTY", ts, price, price + 0.6, price - 0.6, price))
    return bars


@pytest.fixture(scope="module")
def full_bars() -> list[dict[str, object]]:
    return [*_session_bars(_PREVIOUS, 24_000.0, 0.0, 375), *_session_bars(_DAY, 24_050.0, 0.9, 375)]


def _store(tmp_path, bars: list[dict[str, object]], tag: str) -> BarStore:  # noqa: ANN001
    store = BarStore(tmp_path / tag)
    store.append(pd.DataFrame(bars))
    return store


def test_every_rule_module_contributed_at_least_one_strategy() -> None:
    """The guard against silent discovery failure. A rule file that stops
    registering — renamed base class, typo'd spec, forgotten subclass — fails
    here rather than vanishing from the page."""
    modules = rules.import_all()
    assert modules, "no rule modules were discovered at all"
    by_module: dict[str, int] = {}
    for subclass in _every_subclass(SessionRule):
        if isinstance(subclass.__dict__.get("spec"), StrategySpec):
            module = subclass.__module__.rsplit(".", 1)[-1]
            by_module[module] = by_module.get(module, 0) + 1
    silent = sorted(set(modules) - set(by_module))
    assert not silent, f"these rule modules registered nothing: {silent}"


def _every_subclass(cls: type) -> list[type]:
    found: list[type] = []
    for subclass in cls.__subclasses__():
        found.append(subclass)
        found.extend(_every_subclass(subclass))
    return found


def test_the_library_is_large_enough_to_be_a_real_search() -> None:
    """A guard on the guards: if the registry were emptied, every
    parametrised test below would collect nothing and pass vacuously."""
    assert len(available()) >= 30, f"expected the full library, found {len(available())}: {available()}"


def test_the_control_is_present_and_cannot_trade() -> None:
    """The coin flip is the yardstick every other strategy is measured
    against, and it must never be mistaken for a suggestion."""
    control = spec("random_entry")
    assert control.family == "control"
    assert control.tradeable is False, "the random control must never be tradeable"


@pytest.mark.parametrize("name", sorted(available()))
def test_every_spec_is_renderable_and_searchable(name: str) -> None:
    strategy_spec = spec(name)
    assert strategy_spec.summary.strip(), f"{name} has no plain-English summary for the page"
    assert not strategy_spec.summary.endswith((".py", "()")), f"{name}'s summary reads like code, not English"
    for param in strategy_spec.params:
        assert param.description.strip(), f"{name}.{param.name} has no description"
        if param.kind in ("int", "decimal"):
            assert param.low is not None and param.high is not None
            assert param.low <= float(param.default) <= param.high, (
                f"{name}.{param.name} default {param.default} sits outside its own declared range"
            )
    # The trial count fed to the deflated Sharpe ratio comes from here, so a
    # runaway space would silently make every result look significant.
    assert strategy_spec.search_space_size() <= 1000, f"{name} declares an implausibly large search space"


@pytest.mark.parametrize("name", sorted(available()))
def test_a_strategy_cannot_see_bars_that_do_not_exist_yet(name: str, tmp_path, full_bars) -> None:  # noqa: ANN001
    """The load-bearing one.

    Evaluate at 12:00 twice: once against a store holding the WHOLE day, and
    once against a store truncated at 12:00. A strategy reading only through
    `ctx.bars()` cannot tell the difference. One reaching for `ctx.store`
    directly, or caching across calls, will.
    """
    as_of = dt.datetime.combine(_DAY, dt.time(12, 0), tzinfo=IST)
    truncated = [b for b in full_bars if b["event_ts"] <= as_of.astimezone(dt.UTC)]

    complete_store = _store(tmp_path, full_bars, f"full-{name}")
    partial_store = _store(tmp_path, truncated, f"cut-{name}")

    from te.strategy.context import StrategyContext

    with_future = get(name).evaluate(
        StrategyContext(store=complete_store, instrument="NIFTY", exchange="NSE_INDEX", as_of=as_of)
    )
    without_future = get(name).evaluate(
        StrategyContext(store=partial_store, instrument="NIFTY", exchange="NSE_INDEX", as_of=as_of)
    )

    assert with_future.verdict == without_future.verdict, (
        f"{name} decided differently when later bars existed in the store — it is reading the future"
    )
    assert with_future.reason == without_future.reason, f"{name} gave a different reason when the future was visible"


@pytest.mark.parametrize("name", sorted(available()))
def test_every_evaluation_is_auditable(name: str, tmp_path, full_bars) -> None:  # noqa: ANN001
    """Every decision must carry a reason, and every condition claiming to
    have been evaluated must carry a real measured value. This is the
    invariant the whole decision-explainability UI rests on."""
    as_of = dt.datetime.combine(_DAY, dt.time(12, 0), tzinfo=IST)
    store = _store(tmp_path, full_bars, f"audit-{name}")

    from te.strategy.context import StrategyContext

    evaluation = get(name).evaluate(
        StrategyContext(store=store, instrument="NIFTY", exchange="NSE_INDEX", as_of=as_of)
    )
    assert evaluation.reason.strip(), f"{name} produced a decision with no stated reason"
    for condition in evaluation.conditions:
        if condition.evaluated:
            assert condition.actual.strip(), f"{name} claims to have evaluated {condition.label!r} with no value"


@pytest.mark.parametrize("name", sorted(available()))
def test_a_fresh_instance_carries_no_state_from_the_last_one(name: str) -> None:
    """`get()` must hand back a clean strategy — a stale `last_signal`
    leaking across cycles would attach one instrument's signal to another."""
    assert getattr(get(name), "last_signal", None) is None
