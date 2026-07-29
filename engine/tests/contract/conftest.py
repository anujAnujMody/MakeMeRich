"""Resets the Phase-0 in-memory API state before every contract test, so one
test's mutation (an order placed, mode switched, ...) never leaks into the
next — mirrors `dashboard/src/mocks/handlers.ts`'s `resetMockState()`."""

from collections.abc import Iterator

import pytest

from te.api.state import state


@pytest.fixture(autouse=True)
def _reset_api_state() -> Iterator[None]:
    state.reset()
    yield
    state.reset()
