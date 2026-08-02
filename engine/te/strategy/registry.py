"""`name -> Strategy` lookup, populated by discovering every plug-in in
`te.strategy.rules`.

Stores FACTORIES, not shared singleton instances — every call to `get()`
returns a fresh `Strategy`, so one strategy's per-evaluation state (e.g.
`last_signal`) can never leak across cycles or instruments.

### Discovery, and why it is not a decorator

Registration walks `SessionRule.__subclasses__()` after importing every
module in `te.strategy.rules`. The obvious alternative — a `@register`
decorator — has a known failure mode: a decorator only runs when its module
is imported, so a strategy in a file nothing imports is silently absent. No
error, no warning. On a page listing thirty strategies, one quietly missing
is close to undetectable. See `te/strategy/rules/__init__.py`.

`te.strategy.orb.OrbStrategy` is registered by hand alongside the discovered
ones. It predates this system and is the only strategy with recorded live
history under the name `"orb"`; re-pointing that name at the equivalent
`orb60` plug-in would silently attach two years of recorded firings to a
different implementation.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import cast

from te.strategy import rules
from te.strategy.base import Strategy
from te.strategy.orb import OrbStrategy
from te.strategy.session_rule import SessionRule
from te.strategy.spec import StrategySpec

_FACTORIES: dict[str, Callable[[], Strategy]] = {}
_SPECS: dict[str, StrategySpec] = {}


def _all_subclasses(cls: type) -> list[type]:
    """Every descendant, not just direct children — several rules share an
    intermediate base (`_OpeningRangeBreakout`, `_Inverted`), and a
    one-level scan would miss all of them.

    Typed as bare `type` rather than `type[SessionRule]` because `SessionRule`
    is abstract, and mypy's `type-abstract` check rejects passing an abstract
    class where a concrete `type[...]` is expected — a rule aimed at
    accidental instantiation, which is not what a subclass walk does.
    """
    found: list[type] = []
    for subclass in cls.__subclasses__():
        found.append(subclass)
        found.extend(_all_subclasses(subclass))
    return found


def _discover() -> None:
    rules.import_all()
    for subclass in _all_subclasses(SessionRule):
        spec_obj = subclass.__dict__.get("spec")
        # Intermediate bases (`_OpeningRangeBreakout`, `_Inverted`,
        # `_WindowedMomentum`) are real subclasses but declare no spec of
        # their OWN — checking `__dict__` rather than `getattr` is what
        # distinguishes them, since `getattr` would find an inherited spec
        # and register shared implementation as if it were a strategy.
        if not isinstance(spec_obj, StrategySpec):
            continue
        if spec_obj.name in _FACTORIES:
            # A duplicate name is always a bug: the name is the primary key
            # for recorded firings, so two strategies sharing one would
            # merge their histories into a single unreadable dataset.
            raise RuntimeError(
                f"two strategies both registered as {spec_obj.name!r} — names identify recorded firings and "
                f"must be unique (second was {subclass.__module__}.{subclass.__qualname__})"
            )
        # `cast` because `SessionRule` is abstract, so mypy will not accept a
        # `type[SessionRule]` where a zero-argument factory is expected. Any
        # class reaching this line declares its own `spec` and therefore
        # implements `decide` — an abstract one could not be instantiated,
        # and `tests/strategy/test_plugin_registry.py` constructs every
        # registered strategy, so a genuinely abstract entry fails there.
        _FACTORIES[spec_obj.name] = cast("Callable[[], Strategy]", subclass)
        _SPECS[spec_obj.name] = spec_obj


_discover()

#: Registered by hand — see the module docstring.
_FACTORIES["orb"] = OrbStrategy
_SPECS["orb"] = StrategySpec(
    name="orb",
    family="breakout",
    summary="The original opening-range breakout, kept because it owns the recorded live history.",
)


def get(name: str) -> Strategy:
    try:
        factory = _FACTORIES[name]
    except KeyError:
        raise KeyError(f"no strategy registered under name={name!r} (available: {available()})") from None
    return factory()


def available() -> list[str]:
    return sorted(_FACTORIES)


def spec(name: str) -> StrategySpec:
    try:
        return _SPECS[name]
    except KeyError:
        raise KeyError(f"no strategy registered under name={name!r} (available: {available()})") from None


def all_specs() -> list[StrategySpec]:
    """Every registered strategy's spec, sorted by family then name — the
    order the Strategies page renders them in."""
    return sorted(_SPECS.values(), key=lambda s: (s.family, s.name))
