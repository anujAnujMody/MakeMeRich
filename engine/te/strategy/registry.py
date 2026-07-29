"""`name -> Strategy` lookup. Stores factories (not shared singleton
instances) — every call to `get()` returns a fresh `Strategy` instance, so
one strategy's per-evaluation state (e.g. `OrbStrategy.last_signal`) can
never leak across cycles or instruments."""

from __future__ import annotations

from collections.abc import Callable

from te.strategy.base import Strategy
from te.strategy.orb import OrbStrategy

_FACTORIES: dict[str, Callable[[], Strategy]] = {
    "orb": OrbStrategy,
}


def get(name: str) -> Strategy:
    try:
        factory = _FACTORIES[name]
    except KeyError:
        raise KeyError(f"no strategy registered under name={name!r} (available: {available()})") from None
    return factory()


def available() -> list[str]:
    return sorted(_FACTORIES)
