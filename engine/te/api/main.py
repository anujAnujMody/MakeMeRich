"""FastAPI app entrypoint. CORS origins come from `Settings.cors_origins` —
never hardcoded, per the plan."""

import datetime as dt
import threading
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
from te.domain.clock import IST
from te.engine.scheduler import (
    build_scheduler,
    relogin_catchup_due,
    run_openalgo_relogin,
    should_start_recorder_now,
)
from te.engine.trading_calendar import get_calendar
from te.ops.logging import configure_logging
from te.persistence.db import engine_from_settings, make_session_factory
from te.settings import Settings


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    # Before anything else touches a logger — `configure_logging` only
    # affects loggers created/used after it runs (see te/ops/logging.py).
    configure_logging(settings.env)

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
        # Exposed for GET /api/broker-status (te/api/routers/broker.py) —
        # `WSRecorderSupervisor.is_running()` is the real broker-connection
        # signal; before this the endpoint was a literal Phase-0 stub always
        # returning `connected=False`, found live on 2026-07-30 showing
        # "Disconnected"/"Down" on the Ops page all day despite the broker
        # actually streaming ticks continuously.
        app.state.ws_supervisor = supervisor
        scheduler.start()
        # Catch-up: the 09:10 IST cron trigger fires once and
        # `BackgroundScheduler` has no memory of a missed fire — a same-day
        # restart after 09:10 would otherwise silently lose the rest of the
        # session's bars until tomorrow. See `should_start_recorder_now`.
        session_factory = make_session_factory(db_engine)
        with session_factory() as session:
            calendar = get_calendar(session)
        if should_start_recorder_now(dt.datetime.now(IST), calendar):
            supervisor.start()
        # The same catch-up, for the broker login, and for the same reason.
        # On 2026-08-05 the 08:40 cron did not fire and every quote returned
        # HTTP 500 until a human ran `POST /api/engine/relogin-broker` at
        # 08:55. Gated on "no successful login TODAY", so a dev `--reload`
        # loop re-runs it at most once a day rather than replaying real
        # credentials at Angel's rate limiter on every reload — the objection
        # that kept this unbuilt. See `relogin_catchup_due`.
        #
        # Run in a THREAD: this is a lifespan coroutine, and a blocking
        # multi-request login here would stall the event loop and delay the
        # app becoming ready — during which the dashboard shows a dead engine.
        if relogin_catchup_due(session_factory, dt.datetime.now(IST)):
            threading.Thread(
                target=run_openalgo_relogin,
                args=(settings, session_factory),
                name="relogin-catchup",
                daemon=True,
            ).start()
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
