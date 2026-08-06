"""Provenance headers — the additive signal that lets the dashboard tell a
real number apart from an honest zero-state, without touching the frozen
response body contract.

`dashboard/src/lib/api.ts`'s `get()`/`post()` throw on any non-2xx status, so
a `501 Not Implemented` would render as a red error instead of an
`EmptyState`. Resolution (per the plan's "Satisfying the 53 endpoints
honestly" section): every not-yet-earned endpoint returns `200` with the
type's honest zero-state body, plus these headers layered on top.
"""

from typing import Literal

from fastapi import Response

Provenance = Literal["none", "paper", "backtest", "live", "in-sample"]


#: What a route says when it has nothing real to show and gave no reason of
#: its own. Only ever emitted alongside `provenance="none"`.
DEFAULT_NOT_READY_REASON = "phase-0: skeleton endpoint, no data recorded yet"


def set_provenance(
    response: Response,
    provenance: Provenance = "none",
    sample_size: int = 0,
    not_ready_reason: str | None = None,
) -> None:
    """Sets the `X-TE-*` provenance headers on `response`.

    `not_ready_reason` is DERIVED from `provenance` when not given, and that
    matters more than it looks. It used to default to the phase-0 message
    unconditionally, so a route serving real data announced
    `provenance=paper` and `not-ready-reason="no data recorded yet"` in the
    same response — contradictory, and the dashboard reads that header to
    decide whether to render an EmptyState over real numbers.
    Only `approvals.py` had noticed and passed `not_ready_reason=""` by hand;
    every other real-data route was emitting the stale message. Found on
    2026-08-01 when `/api/agents/strategies` began returning 31 real
    backtests and still claimed to have none.

    An explicit value always wins, including an explicit reason alongside
    real data (a route can legitimately serve some data and still flag
    something missing).
    """
    if not_ready_reason is None:
        not_ready_reason = DEFAULT_NOT_READY_REASON if provenance == "none" else ""
    response.headers["X-TE-Provenance"] = provenance
    response.headers["X-TE-Sample-Size"] = str(sample_size)
    response.headers["X-TE-Not-Ready-Reason"] = not_ready_reason
