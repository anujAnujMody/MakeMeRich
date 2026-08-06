from __future__ import annotations

import pytest

from te.strategy.orb import OrbStrategy
from te.strategy.registry import available, get


def test_orb_is_registered() -> None:
    assert "orb" in available()


def test_get_returns_an_orb_strategy_instance() -> None:
    strategy = get("orb")
    assert isinstance(strategy, OrbStrategy)
    assert strategy.name == "orb"


def test_get_returns_a_fresh_instance_each_call() -> None:
    """Each cycle × instrument must get its own strategy instance so
    `last_signal` from one instrument's evaluation can never leak into
    another's."""
    a = get("orb")
    b = get("orb")
    assert a is not b


def test_get_unknown_strategy_raises() -> None:
    with pytest.raises(KeyError):
        get("nonexistent")
