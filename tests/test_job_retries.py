"""Transient provider failures: automatic retries that resume at the failed stage, and manual
retries of failed jobs (POST /jobs/{id}/retry). Completed stages are never paid for twice."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest
from pydantic import BaseModel
from sqlalchemy import update

from autoswe.llm.base import LLMOutputError, LLMRequest, LLMResponse, LLMTransientError
from autoswe.llm.offline import OfflineProvider
from autoswe.models import Job, JobStatus, PrincipalUsage, utcnow
from autoswe.pipeline.orchestrator import Orchestrator
from autoswe.repository import BudgetRefusedError, JobRepository, Reservation
from tests.conftest import ALICE, BOB, AppHarness, HarnessFactory
from tests.fakes import PricedProvider, ScriptedProvider, registry_with

RETRYING = {"job_transient_retries": 2, "job_retry_delay_s": 0}


def _flaky(failures: int, exc: type[Exception] = LLMTransientError) -> Any:
    """An override that raises ``failures`` times, then answers like the offline provider."""
    remaining = [failures]

    async def override(request: LLMRequest[Any]) -> BaseModel:
        if remaining[0] > 0:
            remaining[0] -= 1
            raise exc("provider overloaded (529)")
        output: BaseModel = (await OfflineProvider().generate(request)).output
        return output

    return override


async def _events(harness: AppHarness, job_id: str) -> list[tuple[str, dict[str, Any]]]:
    async with harness.app.state.services.db.session() as session:
        rows = await JobRepository(session).events_after(job_id, 0, limit=500)
    return [(row.type, row.payload) for row in rows]


async def test_a_transient_failure_resumes_at_the_failed_stage(
    harness_factory: HarnessFactory,
) -> None:
    provider = ScriptedProvider({"architect": _flaky(1)})
    harness = await harness_factory(registry=registry_with(provider), **RETRYING)
    job = await harness.wait_for((await harness.submit())["id"], {"succeeded", "failed"})

    assert job["status"] == "succeeded", job["error"]
    assert job["retries"] == 1 and job["run_after"] is None
    assert provider.calls.count("analyst") == 1  # the completed stage was not run (or billed) again
    assert provider.calls.count("architect") == 2
    assert {stage["status"] for stage in job["stages"]} == {"succeeded"}
    events = await _events(harness, job["id"])
    kinds = [kind for kind, _ in events]
    scheduled = next(payload for kind, payload in events if kind == "job.retry_scheduled")
    assert scheduled["stage"] == "architect" and scheduled["attempt"] == 1
    assert "overloaded" in scheduled["error"]
    resumed = next(payload for kind, payload in events if kind == "job.resumed")
    assert resumed == {"stages": ["analyze"], "attempt": 2}
    assert (
        kinds.index("job.retry_scheduled")
        < kinds.index("job.resumed")
        < kinds.index("job.succeeded")
    )
    assert kinds.count("job.started") == 2


async def test_retries_are_bounded(harness_factory: HarnessFactory) -> None:
    provider = ScriptedProvider({"coder": _flaky(10)})
    harness = await harness_factory(
        registry=registry_with(provider), job_transient_retries=1, job_retry_delay_s=0
    )
    job = await harness.wait_for((await harness.submit())["id"], {"succeeded", "failed"})
    assert job["status"] == "failed"
    assert "overloaded" in job["error"] and job["retries"] == 1
    assert provider.calls.count("coder") == 2
    assert provider.calls.count("analyst") == provider.calls.count("architect") == 1
    statuses = [stage["status"] for stage in job["stages"]]
    assert statuses == ["succeeded", "succeeded", "failed", "skipped", "skipped"]


async def test_errors_that_retrying_cannot_fix_fail_at_once(
    harness_factory: HarnessFactory,
) -> None:
    provider = ScriptedProvider({"architect": _flaky(1, LLMOutputError)})
    harness = await harness_factory(registry=registry_with(provider), **RETRYING)
    job = await harness.wait_for((await harness.submit())["id"], {"succeeded", "failed"})
    assert job["status"] == "failed" and job["retries"] == 0
    assert provider.calls.count("architect") == 1


async def test_a_failed_job_is_retried_from_the_failed_stage(
    harness_factory: HarnessFactory,
) -> None:
    provider = ScriptedProvider({"devops": _flaky(1)})
    harness = await harness_factory(registry=registry_with(provider), job_transient_retries=0)
    job_id = (await harness.submit())["id"]
    failed = await harness.wait_for(job_id, {"succeeded", "failed"})
    assert failed["status"] == "failed"

    other = await harness.client.post(f"/api/v1/jobs/{job_id}/retry", headers=BOB)
    assert other.status_code == 404
    response = await harness.client.post(f"/api/v1/jobs/{job_id}/retry", headers=ALICE)
    assert response.status_code == 202, response.text
    assert response.json()["status"] in {"queued", "running"}
    assert response.json()["error"] is None

    job = await harness.wait_for(job_id, {"succeeded", "failed"})
    assert job["status"] == "succeeded", job["error"]
    for agent in ("analyst", "architect", "coder"):
        assert provider.calls.count(agent) == 1, agent
    assert provider.calls.count("devops") == 2
    kinds = [kind for kind, _ in await _events(harness, job_id)]
    assert kinds.count("job.failed") == 1 and "job.retried" in kinds
    files = (await harness.client.get(f"/api/v1/jobs/{job_id}/files", headers=ALICE)).json()
    assert {"app/main.py", "Dockerfile"} <= {file["path"] for file in files}

    again = await harness.client.post(f"/api/v1/jobs/{job_id}/retry", headers=ALICE)
    assert again.status_code == 409
    assert again.json()["detail"] == "Only a failed job can be retried; it is succeeded"


async def test_a_requeued_job_waits_until_its_retry_time(harness_factory: HarnessFactory) -> None:
    harness = await harness_factory(worker_enabled=False)
    job_id = (await harness.submit())["id"]
    db = harness.app.state.services.db
    async with db.session() as session:
        repo = JobRepository(session)
        assert await repo.claim_next("w1") == job_id
        retry = await repo.schedule_retry(job_id, stage="analyze", delay_s=60, error="429")
        assert retry == 1
        assert await repo.schedule_retry(job_id, stage="analyze", delay_s=60, error="x") is None
        assert await repo.claim_next("w1") is None  # not before run_after
        await session.execute(
            update(Job).where(Job.id == job_id).values(run_after=utcnow() - timedelta(seconds=1))
        )
        await session.commit()
        assert await repo.claim_next("w1") == job_id
    detail = (await harness.client.get(f"/api/v1/jobs/{job_id}", headers=ALICE)).json()
    assert detail["status"] == "running" and detail["retries"] == 1
    assert detail["run_after"] is None  # only reported while the job waits


async def test_a_retry_needs_room_in_the_budget(harness_factory: HarnessFactory) -> None:
    class FlakyPriced(PricedProvider):
        fail = True

        async def generate[T: BaseModel](self, request: LLMRequest[T]) -> LLMResponse[T]:
            if request.agent == "reviewer" and self.fail:
                raise LLMTransientError("overloaded")
            return await super().generate(request)

    provider = FlakyPriced()
    harness = await harness_factory(
        registry=registry_with(provider), token_budgets="alice:1000000",
        job_token_reservation=50_000, job_transient_retries=0,
    )  # fmt: skip
    job_id = (await harness.submit())["id"]
    assert (await harness.wait_for(job_id, {"succeeded", "failed"}))["status"] == "failed"
    async with harness.app.state.services.db.session() as session:
        await session.execute(
            update(PrincipalUsage)
            .where(PrincipalUsage.principal == "alice")
            .values(used_tokens=999_999)
        )
        await session.commit()
    refused = await harness.client.post(f"/api/v1/jobs/{job_id}/retry", headers=ALICE)
    assert refused.status_code == 402
    assert refused.json()["code"] == "token_budget_exceeded"
    job = (await harness.client.get(f"/api/v1/jobs/{job_id}", headers=ALICE)).json()
    assert job["status"] == "failed"  # unchanged: the refused reservation left no trace

    async with harness.app.state.services.db.session() as session:
        await session.execute(update(PrincipalUsage).values(used_tokens=0))
        await session.commit()
    provider.fail = False
    calls_before = provider.calls
    assert (
        await harness.client.post(f"/api/v1/jobs/{job_id}/retry", headers=ALICE)
    ).status_code == 202
    job = await harness.wait_for(job_id, {"succeeded", "failed"})
    assert job["status"] == "succeeded", job["error"]
    assert provider.calls - calls_before == 1  # only the reviewer ran again


async def test_retry_of_a_job_that_is_not_failed_is_refused(
    harness_factory: HarnessFactory,
) -> None:
    harness = await harness_factory(worker_enabled=False, max_active_jobs_per_owner=1)
    job_id = (await harness.submit())["id"]
    response = await harness.client.post(f"/api/v1/jobs/{job_id}/retry", headers=ALICE)
    assert response.status_code == 409
    assert response.json()["detail"] == "Only a failed job can be retried; it is queued"
    async with harness.app.state.services.db.session() as session:
        repo = JobRepository(session)
        assert not await repo.retry_failed(job_id, "alice")  # still queued
        assert not await repo.retry_failed(job_id, "bob")  # not bob's
        await session.execute(update(Job).where(Job.id == job_id).values(status=JobStatus.FAILED))
        await session.commit()
        with pytest.raises(BudgetRefusedError):
            await repo.retry_failed(job_id, "alice", Reservation(utcnow(), 1_000, 10))
        assert await repo.status_of(job_id) is JobStatus.FAILED
    await harness.submit()  # the one active job this key may have
    busy = await harness.client.post(f"/api/v1/jobs/{job_id}/retry", headers=ALICE)
    assert busy.status_code == 429


async def test_a_checkpoint_that_no_longer_validates_is_rerun(
    harness_factory: HarnessFactory,
) -> None:
    provider = ScriptedProvider({"architect": _flaky(1)})
    harness = await harness_factory(registry=registry_with(provider), job_transient_retries=0)
    job_id = (await harness.submit())["id"]
    assert (await harness.wait_for(job_id, {"succeeded", "failed"}))["status"] == "failed"
    async with harness.app.state.services.db.session() as session:
        await session.execute(
            update(Job).where(Job.id == job_id).values(requirements={"schema": "from v0"})
        )
        await session.commit()
    assert (
        await harness.client.post(f"/api/v1/jobs/{job_id}/retry", headers=ALICE)
    ).status_code == 202
    job = await harness.wait_for(job_id, {"succeeded", "failed"})
    assert job["status"] == "succeeded", job["error"]
    assert provider.calls.count("analyst") == 2  # its stored requirements could not be used


def test_backoff_doubles_with_jitter_and_is_capped() -> None:
    orchestrator = Orchestrator.__new__(Orchestrator)
    orchestrator._retry_delay_s = 30.0
    for attempt, base in ((1, 30.0), (2, 60.0), (3, 120.0), (9, 600.0)):
        delays = [orchestrator._retry_delay(attempt) for _ in range(50)]
        assert all(0.8 * base <= delay <= 1.2 * base for delay in delays), attempt
