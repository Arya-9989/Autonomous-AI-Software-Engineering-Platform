from __future__ import annotations

import io
import json
import zipfile
from typing import Any

import pytest

from tests.conftest import ALICE, LIBRARY_SPEC, AppHarness, HarnessFactory


async def test_job_runs_to_completion_with_stage_progress(harness: AppHarness) -> None:
    created = await harness.submit(title="Library")
    assert created["status"] in {"queued", "running"}
    assert created["title"] == "Library"
    assert created["provider"] == "offline"
    assert [s["name"] for s in created["stages"]] == [
        "analyze", "architect", "implement", "devops", "review",
    ]  # fmt: skip

    job = await harness.wait_for(created["id"], {"succeeded", "failed"})
    assert job["status"] == "succeeded", job["error"]
    assert job["progress"] == 1.0
    assert job["project_name"] == "library-api"
    assert all(stage["status"] == "succeeded" for stage in job["stages"])
    assert all(stage["duration_s"] is not None for stage in job["stages"])
    assert job["validation"] == {"passed": True, "errors": 0, "warnings": 0, "files_checked": 32}
    assert job["review_score"] == 100
    assert job["started_at"] is not None and job["finished_at"] is not None


async def test_generated_artifacts_are_browsable(harness: AppHarness) -> None:
    job_id = (await harness.submit())["id"]
    await harness.wait_for(job_id, {"succeeded"})
    client = harness.client

    files = (await client.get(f"/api/v1/jobs/{job_id}/files", headers=ALICE)).json()
    by_path = {f["path"]: f for f in files}
    assert by_path["app/main.py"]["language"] == "python"
    assert by_path["Dockerfile"]["language"] == "docker"
    assert by_path["deploy/terraform/main.tf"]["language"] == "hcl"
    assert by_path["Dockerfile"]["stage"] == "devops"
    assert "content" not in by_path["app/main.py"]

    main = (await client.get(f"/api/v1/jobs/{job_id}/files/app/main.py", headers=ALICE)).json()
    assert "FastAPI(" in main["content"]
    missing = await client.get(f"/api/v1/jobs/{job_id}/files/nope.py", headers=ALICE)
    assert missing.status_code == 404

    requirements = (await client.get(f"/api/v1/jobs/{job_id}/requirements", headers=ALICE)).json()
    assert [e["name"] for e in requirements["entities"]] == ["Member", "Book", "Librarian", "Loan"]
    architecture = (await client.get(f"/api/v1/jobs/{job_id}/architecture", headers=ALICE)).json()
    assert {"method": "GET", "path": "/books", "summary": "List books", "entity": "Book"} in (
        architecture["endpoints"]
    )
    review = (await client.get(f"/api/v1/jobs/{job_id}/review", headers=ALICE)).json()
    assert review["score"] == 100
    assert review["validation"]["files_checked"] == 32


async def test_archive_download(harness: AppHarness) -> None:
    job_id = (await harness.submit())["id"]
    await harness.wait_for(job_id, {"succeeded"})
    response = await harness.client.get(f"/api/v1/jobs/{job_id}/archive", headers=ALICE)
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/zip"
    assert 'filename="library-api.zip"' in response.headers["content-disposition"]
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        names = archive.namelist()
        assert "library-api/Dockerfile" in names
        assert "library-api/app/routers/loans.py" in names
        spec = json.loads(archive.read("library-api/docs/architecture.json"))
        assert spec["deployment"]["cloud_target"] == "aws-ecs-fargate"


@pytest.mark.parametrize(
    ("payload", "fragment"),
    [
        ({"spec": "too short"}, "at least 20"),
        ({"spec": " " * 30}, "at least 20"),
        ({"spec": "x" * 20_001}, "at most 20000"),
        ({"spec": LIBRARY_SPEC, "unexpected": True}, "Extra inputs"),
        ({}, "Field required"),
    ],
)
async def test_invalid_submissions_are_rejected(
    harness: AppHarness, payload: dict[str, Any], fragment: str
) -> None:
    response = await harness.client.post("/api/v1/jobs", json=payload, headers=ALICE)
    assert response.status_code == 422
    assert fragment in json.dumps(response.json())


async def test_unknown_provider_is_rejected(harness: AppHarness) -> None:
    response = await harness.client.post(
        "/api/v1/jobs", json={"spec": LIBRARY_SPEC, "provider": "gpt"}, headers=ALICE
    )
    assert response.status_code == 422
    assert "Available: ['offline']" in response.json()["detail"]


async def test_active_job_limit(harness_factory: HarnessFactory) -> None:
    harness = await harness_factory(worker_enabled=False, max_active_jobs_per_owner=2)
    await harness.submit()
    await harness.submit()
    response = await harness.client.post("/api/v1/jobs", json={"spec": LIBRARY_SPEC}, headers=ALICE)
    assert response.status_code == 429


async def test_cancel_queued_job_and_lifecycle_conflicts(harness_factory: HarnessFactory) -> None:
    harness = await harness_factory(worker_enabled=False)
    job_id = (await harness.submit())["id"]
    client = harness.client

    archive = await client.get(f"/api/v1/jobs/{job_id}/archive", headers=ALICE)
    assert archive.status_code == 409
    assert (await client.delete(f"/api/v1/jobs/{job_id}", headers=ALICE)).status_code == 409

    cancelled = (await client.post(f"/api/v1/jobs/{job_id}/cancel", headers=ALICE)).json()
    assert cancelled["status"] == "cancelled"
    assert {s["status"] for s in cancelled["stages"]} == {"skipped"}
    again = await client.post(f"/api/v1/jobs/{job_id}/cancel", headers=ALICE)
    assert again.status_code == 409

    assert (await client.delete(f"/api/v1/jobs/{job_id}", headers=ALICE)).status_code == 204
    assert (await client.get(f"/api/v1/jobs/{job_id}", headers=ALICE)).status_code == 404


async def test_list_is_paginated_newest_first(harness_factory: HarnessFactory) -> None:
    harness = await harness_factory(worker_enabled=False, max_active_jobs_per_owner=10)
    ids = [(await harness.submit(title=f"job {i}"))["id"] for i in range(5)]

    first = (await harness.client.get("/api/v1/jobs?limit=2", headers=ALICE)).json()
    assert [j["id"] for j in first["items"]] == ids[::-1][:2]
    assert first["next_cursor"] is not None

    seen = [j["id"] for j in first["items"]]
    cursor = first["next_cursor"]
    while cursor:
        page = (
            await harness.client.get(
                "/api/v1/jobs", params={"limit": 2, "before": cursor}, headers=ALICE
            )
        ).json()
        seen += [j["id"] for j in page["items"]]
        cursor = page["next_cursor"]
    assert seen == ids[::-1]


async def test_title_defaults_to_first_line_of_spec(harness_factory: HarnessFactory) -> None:
    harness = await harness_factory(worker_enabled=False)
    long_line = "Build " + "a very detailed service " * 10
    job = await harness.submit(spec=long_line + "\nmore details here")
    assert job["title"].endswith("...")
    assert len(job["title"]) == 80
