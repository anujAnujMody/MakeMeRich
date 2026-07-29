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


def set_provenance(
    response: Response,
    provenance: Provenance = "none",
    sample_size: int = 0,
    not_ready_reason: str = "phase-0: skeleton endpoint, no data recorded yet",
) -> None:
    """Sets the `X-TE-*` provenance headers on `response`. Phase 0 calls this
    with the defaults on every route — nothing has earned real data yet."""
    response.headers["X-TE-Provenance"] = provenance
    response.headers["X-TE-Sample-Size"] = str(sample_size)
    response.headers["X-TE-Not-Ready-Reason"] = not_ready_reason
