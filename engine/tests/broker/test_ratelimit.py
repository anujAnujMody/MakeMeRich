"""te.broker.ratelimit.TokenBucket — hard cap 5 orders/sec (SEBI allows
<10/sec; the plan specifies capping the dispatcher at 5, `Settings.
max_orders_per_second` already defaults to 5)."""

from __future__ import annotations

from te.broker.ratelimit import TokenBucket


class _FakeClock:
    def __init__(self, start: float = 0.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def test_ratelimit_caps_at_5_per_second() -> None:
    clock = _FakeClock()
    bucket = TokenBucket(rate=5.0, capacity=5, clock=clock)

    # Bucket starts full: 5 immediate acquisitions succeed.
    for _ in range(5):
        assert bucket.try_acquire() is True

    # The 6th, with no time elapsed, is refused.
    assert bucket.try_acquire() is False

    # After a full second, exactly 5 more tokens are available.
    clock.advance(1.0)
    for _ in range(5):
        assert bucket.try_acquire() is True
    assert bucket.try_acquire() is False


def test_ratelimit_partial_refill() -> None:
    clock = _FakeClock()
    bucket = TokenBucket(rate=5.0, capacity=5, clock=clock)
    for _ in range(5):
        bucket.try_acquire()

    clock.advance(0.2)  # 0.2s * 5/sec = 1 token
    assert bucket.try_acquire() is True
    assert bucket.try_acquire() is False


def test_ratelimit_never_exceeds_capacity() -> None:
    clock = _FakeClock()
    bucket = TokenBucket(rate=5.0, capacity=5, clock=clock)
    clock.advance(100.0)  # would overflow tokens far past capacity without clamping
    count = 0
    while bucket.try_acquire():
        count += 1
    assert count == 5


def test_acquire_blocks_until_a_token_is_free(monkeypatch) -> None:
    clock = _FakeClock()
    bucket = TokenBucket(rate=5.0, capacity=1, clock=clock)
    bucket.try_acquire()  # drain the only token

    sleeps: list[float] = []

    def _fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)
        clock.advance(seconds)

    monkeypatch.setattr("te.broker.ratelimit.time.sleep", _fake_sleep)
    bucket.acquire()  # must not raise / must not spin forever
    assert sleeps  # it had to wait at least once
