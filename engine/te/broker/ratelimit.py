"""Token-bucket rate limiter — hard cap on order submission. SEBI's algo
framework allows <10 orders/sec for unregistered personal use; the plan
specifies capping the dispatcher at 5 (`Settings.max_orders_per_second`
defaults to 5) rather than riding right up to the regulatory edge.
"""

from __future__ import annotations

import time
from collections.abc import Callable


class TokenBucket:
    """Classic token bucket: `capacity` tokens, refilled continuously at
    `rate` tokens/sec, never exceeding `capacity`. `clock` is injectable so
    tests don't need real sleeps."""

    def __init__(
        self,
        *,
        rate: float,
        capacity: int,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if rate <= 0:
            raise ValueError("rate must be positive")
        if capacity <= 0:
            raise ValueError("capacity must be positive")
        self._rate = rate
        self._capacity = float(capacity)
        self._tokens = float(capacity)
        self._clock = clock
        self._last_refill = clock()

    def _refill(self) -> None:
        now = self._clock()
        elapsed = max(0.0, now - self._last_refill)
        self._tokens = min(self._capacity, self._tokens + elapsed * self._rate)
        self._last_refill = now

    def try_acquire(self, n: int = 1) -> bool:
        """Non-blocking: takes `n` tokens if available, returns whether it
        succeeded."""
        self._refill()
        if self._tokens >= n:
            self._tokens -= n
            return True
        return False

    def acquire(self, n: int = 1, *, poll_interval: float = 0.02) -> None:
        """Blocks (via `time.sleep`) until `n` tokens are available."""
        while not self.try_acquire(n):
            time.sleep(poll_interval)
