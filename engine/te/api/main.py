"""FastAPI app entrypoint. CORS origins come from `Settings.cors_origins` —
never hardcoded, per the plan."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from te.api.routers import (
    agents,
    approvals,
    broker,
    dashboard,
    decisions,
    engine,
    execution,
    journal,
    learning,
    market,
    mode,
    orders,
    pnl,
    positions,
    strategies,
    trades,
)
from te.engine.scheduler import build_scheduler
from te.persistence.db import engine_from_settings
from te.settings import Settings


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        # Phase 1: WS recorder supervisor (09:10-15:35 IST), bhavcopy
        # ingest (18:30 IST), instrument sync (08:45 IST). Phase 4/6/7: the
        # paper-trading cycle job (`run_entry_cycle`/`run_exit_cycle`, every
        # `Settings.paper_cycle_interval_minutes`, mode/kill-switch/session
        # gated — see `PaperCycleRunner`). See te/engine/scheduler.py.
        # Nothing here was in Phase 0; this is new wiring, not a
        # modification of Phase 0 behaviour.
        db_engine = engine_from_settings(settings)
        scheduler, supervisor, paper_cycle_runner = build_scheduler(settings, engine=db_engine)
        # Exposed for GET /api/engine/scheduler-status (te/api/routers/engine.py)
        # to confirm the paper-cycle job is registered and read its last-run
        # time/result — the FastAPI-recommended place for per-app singletons
        # that a router needs but that aren't a DB/settings dependency.
        app.state.scheduler = scheduler
        app.state.paper_cycle_runner = paper_cycle_runner
        scheduler.start()
        try:
            yield
        finally:
            scheduler.shutdown(wait=False)
            supervisor.stop()
            db_engine.dispose()

    app = FastAPI(title="Trading Engine API", version="0.1.0", lifespan=lifespan)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-TE-Provenance", "X-TE-Sample-Size", "X-TE-Not-Ready-Reason"],
    )

    for router in (
        mode.router,
        strategies.router,
        dashboard.router,
        decisions.router,
        approvals.router,
        market.router,
        orders.router,
        positions.router,
        trades.router,
        pnl.router,
        journal.router,
        broker.router,
        engine.router,
        agents.router,
        learning.router,
        execution.router,
    ):
        app.include_router(router)

    return app


app = create_app()
