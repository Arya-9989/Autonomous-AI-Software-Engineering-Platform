"""Idempotency-Key on job submission: a client retrying after a timeout gets the job it already
created back, instead of a second job and a second bill."""

from __future__ import annotations

import asyncio

from sqlalchemy import func, select, update

from autoswe.models import Job, JobStatus
from tests.conftest import ALICE, BOB, LIBRARY_SPEC, AppHarness, HarnessFactory


async def _count(harness: AppHarness) -> int:
    async with harness.app.state.services.db.session() as session:
        return int((await session.execute(select(func.count()).select_from(Job))).scalar_one())


async def test_a_repeated_key_returns_the_same_job(harness_factory: HarnessFactory) -> None:
    harness = await harness_factory(worker_enabled=False, max_active_jobs_per_owner=1)
    headers = {**ALICE, "Idempotency-Key": "order-7f3a"}
    first = await harness.client.post("/api/v1/jobs", json={"spec": LIBRARY_SPEC}, headers=headers)
    assert first.status_code == 202 and "Idempotent-Replayed" not in first.headers
    # The retry is not refused by the active-job limit (it creates nothing) and costs nothing.
    again = await harness.client.post("/api/v1/jobs", json={"spec": LIBRARY_SPEC}, headers=headers)
    assert again.status_code == 202
    assert again.headers["Idempotent-Replayed"] == "true"
    assert again.json()["id"] == first.json()["id"]
    assert await _count(harness) == 1


async def test_a_key_reused_for_another_request_is_rejected(
    harness_factory: HarnessFactory,
) -> None:
    harness = await harness_factory(worker_enabled=False)
    headers = {**ALICE, "Idempotency-Key": "k1"}
    assert (await harness.client.post("/api/v1/jobs", json={"spec": LIBRARY_SPEC},
                                      headers=headers)).status_code == 202  # fmt: skip
    other = await harness.client.post(
        "/api/v1/jobs", json={"spec": LIBRARY_SPEC, "stack": "go"}, headers=headers
    )
    assert other.status_code == 422
    assert other.json()["detail"] == "Idempotency-Key was already used for a different request"
    assert await _count(harness) == 1


async def test_keys_belong_to_their_owner_and_are_validated(
    harness_factory: HarnessFactory,
) -> None:
    harness = await harness_factory(worker_enabled=False)
    body = {"spec": LIBRARY_SPEC}
    alice = await harness.client.post("/api/v1/jobs", json=body,
                                      headers={**ALICE, "Idempotency-Key": "same"})  # fmt: skip
    bob = await harness.client.post("/api/v1/jobs", json=body,
                                    headers={**BOB, "Idempotency-Key": "same"})  # fmt: skip
    assert alice.status_code == bob.status_code == 202
    assert alice.json()["id"] != bob.json()["id"]
    for bad in ("has space", "x" * 129, ""):
        response = await harness.client.post(
            "/api/v1/jobs", json=body, headers={**ALICE, "Idempotency-Key": bad}
        )
        assert response.status_code == 422, bad
    plain = [await harness.client.post("/api/v1/jobs", json=body, headers=ALICE) for _ in range(2)]
    assert plain[0].json()["id"] != plain[1].json()["id"]  # no key: every request is a new job


async def test_concurrent_retries_with_one_key_create_one_job(
    harness_factory: HarnessFactory,
) -> None:
    harness = await harness_factory(worker_enabled=False, max_active_jobs_per_owner=10)
    headers = {**ALICE, "Idempotency-Key": "burst"}
    responses = await asyncio.gather(*(
        harness.client.post("/api/v1/jobs", json={"spec": LIBRARY_SPEC}, headers=headers)
        for _ in range(5)
    ))  # fmt: skip
    assert {r.status_code for r in responses} == {202}
    assert len({r.json()["id"] for r in responses}) == 1
    assert sum(r.headers.get("Idempotent-Replayed") == "true" for r in responses) == 4
    assert await _count(harness) == 1


async def test_deleting_the_job_frees_its_key(harness_factory: HarnessFactory) -> None:
    harness = await harness_factory(worker_enabled=False)
    headers = {**ALICE, "Idempotency-Key": "reusable"}
    body = {"spec": LIBRARY_SPEC}
    first = (await harness.client.post("/api/v1/jobs", json=body, headers=headers)).json()
    async with harness.app.state.services.db.session() as session:
        await session.execute(
            update(Job).where(Job.id == first["id"]).values(status=JobStatus.SUCCEEDED)
        )
        await session.commit()
    assert (
        await harness.client.delete(f"/api/v1/jobs/{first['id']}", headers=ALICE)
    ).status_code == 204
    second = await harness.client.post("/api/v1/jobs", json=body, headers=headers)
    assert second.status_code == 202 and second.json()["id"] != first["id"]
