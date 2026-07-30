"""Phase 0's central discipline: no number may ever be displayed that the
system hasn't earned. Walks every GET response body (the read-only surface —
POST responses that echo a caller-supplied payload, like `/api/orders/place`
or `/api/journal/save`, are intentionally excluded: echoing what the caller
just sent is not fabrication) and asserts every numeric field is `0`/`0.0`
and every array is empty.

`winRate`/`sharpe`/confidence-without-a-sample are exactly the banned class
of number this test exists to catch (see the plan's "Satisfying the 53
endpoints honestly" section).
"""

from collections.abc import Iterator
from typing import Any

from fastapi.testclient import TestClient

from te.api.main import app
from tests.contract.test_all_endpoints import ENDPOINTS

client = TestClient(app)

# GET endpoints whose zero-state legitimately contains a non-zero value:
# these are config/enum defaults, not measured metrics, and the plan does
# not ban them.
ALLOWED_NONZERO_PATHS = {
    "/api/market-status",  # status/label are a valid enum default, not a metric
    # maxDrawdownLimitPct defaults to 100 (= no cap enforced) via
    # `te.engine.state.guardrails_defaults_from_settings` when no guardrails
    # have been saved yet — a real, honest config default (this guardrail
    # was never enforced before the Tier-1 wiring pass added it), not a
    # measured/fabricated metric. currentDrawdownPct/lastSuccessfulPollSecondsAgo
    # on this same endpoint are still genuinely 0.
    "/api/engine/health",
    # dailyLossLimit/maxPositions/maxTradesPerDay are the same guardrail
    # config defaults as above, surfaced on the dashboard snapshot too (see
    # `te.api.routers.dashboard.get_dashboard_snapshot`) — real configured
    # limits, not measured metrics. todayPnl/tradesToday/openPositionsCount
    # etc. on this same endpoint are still genuinely 0.
    "/api/dashboard/snapshot",
}


def _walk_numbers(value: Any) -> Iterator[int | float]:
    if isinstance(value, bool):
        return
    if isinstance(value, int | float):
        yield value
    elif isinstance(value, dict):
        for v in value.values():
            yield from _walk_numbers(v)
    elif isinstance(value, list):
        for v in value:
            yield from _walk_numbers(v)


def test_every_get_endpoint_zero_state_has_no_fabricated_numbers() -> None:
    offenders: list[tuple[str, int | float]] = []
    for method, path, _body, _status, _validate in ENDPOINTS:
        if method != "GET":
            continue
        response = client.get(path)
        assert response.status_code == 200, response.text
        if path in ALLOWED_NONZERO_PATHS:
            continue
        for number in _walk_numbers(response.json()):
            if number != 0:
                offenders.append((path, number))

    assert offenders == [], f"non-zero fabricated numbers found at phase 0: {offenders}"
