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
        "ws_recorder_start",
        "ws_recorder_stop",
        "bhavcopy_ingest",
        "instrument_sync",
        "paper_cycle",
    }

    paper_cycle_job = next(job for job in body["jobs"] if job["id"] == "paper_cycle")
    assert paper_cycle_job["maxInstances"] == 1

    # No run has happened yet within the lifespan of this short-lived
    # request (the job's IntervalTrigger fires no sooner than one interval
    # after scheduler.start()) — last-run fields are honestly None, not a
    # fabricated value.
    assert body["paperCycleLastRunAt"] is None
    assert body["paperCycleLastResult"] is None
