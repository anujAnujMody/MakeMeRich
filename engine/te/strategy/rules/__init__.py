"""Strategy plug-ins. Every `SessionRule` subclass in this package is
discovered automatically — adding a strategy means adding a class here and
nothing else.

### Why the explicit import loop

The obvious implementation is a decorator that registers each class as it is
defined. It has a known failure mode: **a decorator only runs when its module
is imported**, so a strategy in a file nothing imports is silently absent —
no error, no warning, it simply never appears. On a Strategies page listing
thirty rules, one quietly missing is close to undetectable.

`pkgutil.iter_modules` over this package's own directory removes the
possibility: every module here is imported, so every class is defined, so
every subclass is found. `tests/strategy/test_registry_discovery.py` closes
the loop by asserting that every rule module actually contributed at least
one strategy — a file that fails to register now fails the suite rather than
disappearing.
"""

from __future__ import annotations

import importlib
import pkgutil

__all__ = ["import_all"]


def import_all() -> list[str]:
    """Imports every module in this package. Returns the module names, so a
    test can assert the discovery actually walked something."""
    names: list[str] = []
    for info in pkgutil.iter_modules(__path__):
        if info.name.startswith("_"):
            continue
        importlib.import_module(f"{__name__}.{info.name}")
        names.append(info.name)
    return names
