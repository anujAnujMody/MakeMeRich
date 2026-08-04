"""`GET /api/engine/scheduler-status` — proves the scheduler's jobs
(including the Phase 4/6/7 paper-trading cycle job, `te.engine.scheduler.
PaperCycleRunner`) are actually registered via the real FastAPI lifespan
(`te.api.main`'s `create_app().lifespan`), not just present in code, and
exposes the paper cycle job's last-run time/result."""

from __future__ import annotations

from fastapi.testclient import TestClient

from te.api.main import app


def test_scheduler_status_reports_all_jobs_including_paper_cycle() -> None:
    with TestClient(app) as client:
        response = client.get("/api/engine/scheduler-status")

    assert response.status_code == 200
    body = response.json()
    job_ids = {job["id"] for job in body["jobs"]}
    assert job_ids == {
        "openalgo_relogin",
        # Clears yesterday's daily-loss halt before trading starts — it must
        # be VISIBLE in the status payload, not just registered, because a
        # silent auto-unhalt is exactly the kind of thing an operator should
        # be able to see the engine doing.
        "daily_loss_halt_reset",
        "ws_recorder_start",
        "ws_recorder_stop",
        "bhavcopy_ingest",
        "instrument_sync",
        "trading_calendar_refresh",
        "paper_cycle",
        "ws_late_subscription_refresh",
        "ws_feed_health_check",
    }

    paper_cycle_job = next(job for job in body["jobs"] if job["id"] == "paper_cycle")
    assert paper_cycle_job["maxInstances"] == 1

    # Same guard as the paper cycle, for the same reason: resolving a strike
    # band is ~88 broker round trips (~32s measured), so a slow run must not
    # stack up behind the next 5-minute tick.
    refresh_job = next(job for job in body["jobs"] if job["id"] == "ws_late_subscription_refresh")
    assert refresh_job["maxInstances"] == 1

    health_job = next(job for job in body["jobs"] if job["id"] == "ws_feed_health_check")
    assert health_job["maxInstances"] == 1

    # No run has happened yet within the lifespan of this short-lived
    # request (the job's IntervalTrigger fires no sooner than one interval
    # after scheduler.start()) — last-run fields are honestly None, not a
    # fabricated value.
    assert body["paperCycleLastRunAt"] is None
    assert body["paperCycleLastResult"] is None
    assert body["feedLastCheckedAt"] is None
    assert body["feedLastTickAt"] is None
