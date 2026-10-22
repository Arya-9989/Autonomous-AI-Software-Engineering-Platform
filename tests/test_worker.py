"""Queue semantics: atomic claims, cancellation, failure handling, crash recovery."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import Any

from sqlalchemy import update

from autoswe.llm.base import LLMRefusalError, LLMRequest
from autoswe.models import Job, JobStatus, utcnow
from autoswe.repository import JobRepository
from tests.conftest import ALICE, HarnessFactory
from tests.fakes import ScriptedProvider, registry_with
from tests.test_events import parse_sse


async def test_claim_is_atomic_across_workers(harness_factory: HarnessFactory) -> None:
    harness = await harness_factory(worker_enabled=False)
    job_id = (await harness.submit())["id"]
    db = harness.app.state.services.db

    async def claim(worker: str) -> str | None:
        async with db.session() as session:
            return await JobRepository(session).claim_next(worker)

    results = await asyncio.gather(*(claim(f"w{i}") for i in range(8)))
    assert [r for r in results if r is not None] == [job_id]


async def test_jobs_are_claimed_oldest_first(harness_factory: HarnessFactory) -> None:
    harness = await harness_factory(worker_enabled=False, max_active_jobs_per_owner=5)
    first = (await harness.submit())["id"]
    await harness.submit()
    async with harness.app.state.services.db.session() as session:
        assert await JobRepository(session).claim_next("w") == first


async def test_running_job_can_be_cancelled(harness_factory: HarnessFactory) -> None:
    provider = ScriptedProvider()
    entered, _gate = provider.pause("architect")  # never released: cancel must interrupt it
    harness = await harness_factory(registry=registry_with(provider))
    job_id = (await harness.submit())["id"]
    await asyncio.wait_for(entered.wait(), 5)

    response = await harness.client.post(f"/api/v1/jobs/{job_id}/cancel", headers=ALICE)
    assert response.status_code == 200
    job = await harness.wait_for(job_id, {"cancelled"})
    statuses = {s["name"]: s["status"] for s in job["stages"]}
    assert statuses["analyze"] == "succeeded"
    assert statuses["architect"] == "skipped"
    events = parse_sse(
        (await harness.client.get(f"/api/v1/jobs/{job_id}/events", headers=ALICE)).text
    )
    assert "job.cancel_requested" in [e["event"] for e in events]
    assert events[-2]["event"] == "job.cancelled"


async def test_llm_failure_fails_the_job_with_a_useful_message(
    harness_factory: HarnessFactory,
) -> None:
    def refuse(request: LLMRequest[Any]) -> Any:
        raise LLMRefusalError("Model declined the coder task (category=None)")

    provider = ScriptedProvider({"coder": refuse})
    harness = await harness_factory(registry=registry_with(provider))
    job = await harness.wait_for((await harness.submit())["id"], {"failed"})
    assert job["error"] == "Code generator: Model declined the coder task (category=None)"
    statuses = [s["status"] for s in job["stages"]]
    assert statuses == ["succeeded", "succeeded", "failed", "skipped", "skipped"]


async def test_unexpected_agent_crash_is_contained(harness_factory: HarnessFactory) -> None:
    def explode(request: LLMRequest[Any]) -> Any:
        raise KeyError("boom")

    provider = ScriptedProvider({"devops": explode})
    harness = await harness_factory(registry=registry_with(provider))
    job = await harness.wait_for((await harness.submit())["id"], {"failed"})
    assert job["error"] == "DevOps engineer crashed (KeyError)"
    # The worker survives and keeps processing new jobs.
    provider.overrides.clear()
    follow_up = await harness.wait_for((await harness.submit())["id"], {"succeeded"})
    assert follow_up["status"] == "succeeded"


async def test_shutdown_requeues_in_flight_jobs(harness_factory: HarnessFactory) -> None:
    provider = ScriptedProvider()
    entered, _gate = provider.pause("coder")
    harness = await harness_factory(registry=registry_with(provider))
    job_id = (await harness.submit())["id"]
    await asyncio.wait_for(entered.wait(), 5)

    await harness.worker.stop()  # simulates SIGTERM during a rolling deploy

    job = (await harness.client.get(f"/api/v1/jobs/{job_id}", headers=ALICE)).json()
    assert job["status"] == "queued"
    assert {s["status"] for s in job["stages"]} == {"pending"}
    files = (await harness.client.get(f"/api/v1/jobs/{job_id}/files", headers=ALICE)).json()
    assert files == []


async def test_stale_running_jobs_are_reaped(harness_factory: HarnessFactory) -> None:
    harness = await harness_factory(worker_enabled=False)
    job_id = (await harness.submit())["id"]
    db = harness.app.state.services.db
    async with db.session() as session:
        repo = JobRepository(session)
        assert await repo.claim_next("dead-worker") == job_id
        await session.execute(
            update(Job).where(Job.id == job_id).values(heartbeat_at=utcnow() - timedelta(hours=1))
        )
        await session.commit()
        assert await repo.fail_stale(timedelta(minutes=5)) == [job_id]
        assert await repo.fail_stale(timedelta(minutes=5)) == []
        assert await repo.status_of(job_id) is JobStatus.FAILED

    job = (await harness.client.get(f"/api/v1/jobs/{job_id}", headers=ALICE)).json()
    assert job["error"] == "Worker heartbeat lost; job abandoned."


async def test_two_replicas_share_one_queue(harness_factory: HarnessFactory) -> None:
    """Two app instances on the same database: every job runs exactly once."""
    first = await harness_factory(max_active_jobs_per_owner=10)
    second = await harness_factory(max_active_jobs_per_owner=10)
    ids = [(await first.submit())["id"] for _ in range(4)]
    for job_id in ids:
        await first.wait_for(job_id, {"succeeded"})

    async with first.app.state.services.db.session() as session:
        workers = {
            (await JobRepository(session).get(job_id)).worker_id  # type: ignore[union-attr]
            for job_id in ids
        }
    assert workers <= {first.worker.id, second.worker.id}
    for job_id in ids:
        events = parse_sse(
            (await second.client.get(f"/api/v1/jobs/{job_id}/events", headers=ALICE)).text
        )
        assert [e["event"] for e in events].count("job.started") == 1


async def test_cancel_racing_a_claim_flags_the_running_job(harness_factory: HarnessFactory) -> None:
    """The API read the job as queued, then a worker claimed it before the cancel was written."""
    harness = await harness_factory(worker_enabled=False)
    job_id = (await harness.submit())["id"]
    db = harness.app.state.services.db
    async with db.session() as api_session:
        api_repo = JobRepository(api_session)
        seen = await api_repo.get(job_id)
        assert seen is not None and seen.status is JobStatus.QUEUED
        async with db.session() as worker_session:
            assert await JobRepository(worker_session).claim_next("w1") == job_id
        assert await api_repo.request_cancel(job_id) == "cancel_requested"

    async with db.session() as session:
        job = await JobRepository(session).get(job_id)
        assert job is not None
        # Still owned by the worker, which now stops at its next stage boundary (and the
        # interrupt handler cancels instead of requeueing, because the flag is set).
        assert job.status is JobStatus.RUNNING
        assert job.cancel_requested is True
        assert job.worker_id == "w1"


async def test_cancel_api_reports_each_outcome(harness_factory: HarnessFactory) -> None:
    harness = await harness_factory(worker_enabled=False)
    job_id = (await harness.submit())["id"]
    cancelled = await harness.client.post(f"/api/v1/jobs/{job_id}/cancel", headers=ALICE)
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"
    assert {s["status"] for s in cancelled.json()["stages"]} == {"skipped"}
    again = await harness.client.post(f"/api/v1/jobs/{job_id}/cancel", headers=ALICE)
    assert again.status_code == 409
    events = parse_sse(
        (await harness.client.get(f"/api/v1/jobs/{job_id}/events", headers=ALICE)).text
    )
    assert [e["event"] for e in events].count("job.cancelled") == 1


async def test_reaped_job_keeps_its_outcome_when_the_worker_returns(
    harness_factory: HarnessFactory,
) -> None:
    harness = await harness_factory(worker_enabled=False)
    job_id = (await harness.submit())["id"]
    async with harness.app.state.services.db.session() as session:
        repo = JobRepository(session)
        assert await repo.claim_next("slow-worker") == job_id
        await session.execute(
            update(Job).where(Job.id == job_id).values(heartbeat_at=utcnow() - timedelta(hours=1))
        )
        await session.commit()
        assert await repo.fail_stale(timedelta(minutes=5)) == [job_id]
        # The worker was only paused (GC, network partition) and now tries to finish or requeue.
        assert not await repo.finish(job_id, "job.succeeded", {}, status=JobStatus.SUCCEEDED)
        assert not await repo.reset_for_retry(job_id)
        assert await repo.status_of(job_id) is JobStatus.FAILED

    events = parse_sse(
        (await harness.client.get(f"/api/v1/jobs/{job_id}/events", headers=ALICE)).text
    )
    terminal = [e["event"] for e in events if e["event"] in {"job.failed", "job.succeeded"}]
    assert terminal == ["job.failed"]


async def test_shutdown_drains_jobs_that_finish_in_time(harness_factory: HarnessFactory) -> None:
    provider = ScriptedProvider()
    entered, gate = provider.pause("coder")
    harness = await harness_factory(registry=registry_with(provider), worker_drain_s=10)
    job_id = (await harness.submit())["id"]
    await asyncio.wait_for(entered.wait(), 5)

    stopping = asyncio.create_task(harness.worker.stop())  # SIGTERM while a job runs
    await asyncio.sleep(0.2)
    assert not stopping.done()  # it waits for the job instead of cancelling it
    gate.set()
    await asyncio.wait_for(stopping, 10)
    job = (await harness.client.get(f"/api/v1/jobs/{job_id}", headers=ALICE)).json()
    assert job["status"] == "succeeded"


async def test_a_stopping_worker_claims_nothing_new_and_requeues_what_outlives_the_drain(
    harness_factory: HarnessFactory,
) -> None:
    provider = ScriptedProvider()
    entered, _gate = provider.pause("coder")  # never released: outlives the drain
    harness = await harness_factory(
        registry=registry_with(provider), worker_drain_s=0.3, worker_concurrency=1
    )
    first = (await harness.submit())["id"]
    await asyncio.wait_for(entered.wait(), 5)
    second = (await harness.submit())["id"]  # waits: the only slot is busy

    await harness.worker.stop()

    for job_id in (first, second):
        job = (await harness.client.get(f"/api/v1/jobs/{job_id}", headers=ALICE)).json()
        assert job["status"] == "queued", job_id  # handed back, not lost


async def test_a_worker_only_process_serves_probes_and_metrics_only(
    harness_factory: HarnessFactory,
) -> None:
    harness = await harness_factory(serve_api=False)
    assert (await harness.client.get("/healthz")).status_code == 200
    ready = await harness.client.get("/readyz")
    assert ready.json()["checks"] == {"database": "ok", "worker": "ok"}
    assert (await harness.client.get("/metrics")).status_code == 200
    assert (await harness.client.get("/api/v1/jobs", headers=ALICE)).status_code == 404
    assert (await harness.client.post("/api/v1/jobs", json={}, headers=ALICE)).status_code == 404
