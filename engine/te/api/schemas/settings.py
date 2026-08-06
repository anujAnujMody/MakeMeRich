"""`GET`/`PUT /api/engine/guardrails` — a NEW endpoint (not part of the
original frozen 53-endpoint contract from `dashboard/src/lib/api.ts`), added
by the "Dashboard<->engine wiring remediation" plan section to make the
Settings page's Account Guardrails card actually control the live engine
instead of living only in browser localStorage.

Rupee<->paise conversion happens at this schema boundary deliberately: the
dashboard/user stays in rupees (matches every other rupee-denominated field
on the frozen contract, e.g. `Trade.pnl`), `te.engine.state.AccountGuardrails`
stays in `Paise` (matches `te.domain.money`'s money-typing rule) — one
conversion point, not scattered across the router."""

from pydantic import BaseModel, Field


class AccountGuardrailsPayload(BaseModel):
    capitalRupees: float = Field(gt=0)
    maxDailyLossRupees: float = Field(ge=0)
    maxPositionSizePct: float = Field(gt=0, le=100)
    maxDrawdownPct: float = Field(gt=0, le=100)
    maxTradesPerDay: int = Field(gt=0)
    maxConcurrentPositions: int = Field(gt=0)
    riskPerTradePct: float = Field(gt=0, le=100)


class InstrumentSelectionPayload(BaseModel):
    """`lotSize` is stored explicitly here but is NOT what actually gets
    traded — `te.engine.state.get_instrument_selections` overrides it with
    the real, broker-verified value from the synced `instruments` table on
    every read (see that function's docstring), so a stale or hand-typed
    value saved here can't silently drift from reality once a sync has run
    for this underlying."""

    symbol: str = Field(min_length=1)
    exchange: str = Field(min_length=1)
    lotSize: int = Field(gt=0)
    active: bool = True


class InstrumentSelectionsPayload(BaseModel):
    instruments: list[InstrumentSelectionPayload]


class ReloginResponse(BaseModel):
    """`POST /api/engine/relogin-broker`'s response — `stage`/`message` let
    the caller distinguish "not configured" from "bad credentials" from
    "Angel rejected the TOTP" instead of a bare pass/fail (see
    `te.broker.openalgo_login.LoginResult`, which this mirrors 1:1)."""

    success: bool
    stage: str
    message: str
