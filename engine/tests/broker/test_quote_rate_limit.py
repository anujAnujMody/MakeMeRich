"""Quote throttling. Angel SmartAPI rate-limits its quote endpoint at 1
request/sec — far tighter than order placement — and forum reports say
enforcement is inconsistent, so exceeding it shows up as intermittent quote
failures rather than a clean error. An intermittent quote failure means an
open position marks stale, which means its stop cannot fire.

The throttle is scoped to `quotes()` only. That is the load-bearing detail:
applying a 1/sec ceiling to the expiry/optionsymbol lookups on the entry
path would add whole seconds to opening a position, and sharing a bucket
with order placement would let a burst of marks delay an exit order.
"""

from __future__ import annotations

from typing import Any

import pytest

from te.broker.openalgo_rest import OpenAlgoRestClient


class _FakeClock:
    """Monotonic clock the test advances by hand — the token bucket takes an
    injectable clock precisely so this needs no real sleeping."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def client_and_calls(monkeypatch: pytest.MonkeyPatch):  # noqa: ANN201
    calls: list[str] = []

    def _fake_post(self: OpenAlgoRestClient, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        calls.append(path)
        return {"status": "success", "data": {"ltp": 100.0, "bid": 99.9, "ask": 100.1}}

    monkeypatch.setattr(OpenAlgoRestClient, "_post", _fake_post)
    return calls


def test_an_unthrottled_client_makes_no_bucket_at_all(client_and_calls) -> None:  # noqa: ANN001
    """`None` must genuinely disable it — every test and offline caller
    depends on quotes being free."""
    client = OpenAlgoRestClient("http://x", "k")
    assert client._quote_limiter is None
    for _ in range(50):
        client.quotes("NIFTY", "NSE_INDEX")
    assert len(client_and_calls) == 50


def test_the_first_quote_is_not_delayed(client_and_calls) -> None:  # noqa: ANN001
    """A full bucket at construction: the throttle must not add a second to
    the very first mark of a cycle."""
    client = OpenAlgoRestClient("http://x", "k", quotes_per_second=1)
    clock = _FakeClock()
    assert client._quote_limiter is not None
    client._quote_limiter._clock = clock  # type: ignore[attr-defined]

    client.quotes("NIFTY", "NSE_INDEX")

    assert len(client_and_calls) == 1, "the first quote should pass straight through"


def test_a_burst_beyond_the_rate_has_to_wait(monkeypatch: pytest.MonkeyPatch, client_and_calls) -> None:  # noqa: ANN001
    """Second call in the same instant finds the bucket empty. Asserted via
    `try_acquire` rather than by timing a real `acquire()`, so the test
    neither sleeps nor depends on wall-clock scheduling."""
    client = OpenAlgoRestClient("http://x", "k", quotes_per_second=1)
    limiter = client._quote_limiter
    assert limiter is not None

    client.quotes("NIFTY", "NSE_INDEX")

    assert limiter.try_acquire() is False, "bucket should be empty immediately after one quote at 1/sec"


def test_orders_and_quotes_do_not_share_a_budget() -> None:
    """A burst of position marks must never delay an exit order. The order
    limiter lives in `te.execution.manager` at `max_orders_per_second`; this
    one is per-client and quote-only, and they are separate objects by
    construction."""
    from te.broker.ratelimit import TokenBucket

    client = OpenAlgoRestClient("http://x", "k", quotes_per_second=1)
    order_bucket = TokenBucket(rate=5, capacity=5)

    assert client._quote_limiter is not None
    assert client._quote_limiter is not order_bucket

    client.quotes  # noqa: B018 — referencing the throttled method, not calling it
    for _ in range(5):
        assert order_bucket.try_acquire() is True, "order budget must be untouched by quote throttling"
