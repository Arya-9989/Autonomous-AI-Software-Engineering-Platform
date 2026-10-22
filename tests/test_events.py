"""Server-Sent Events stream: replay, resume and termination."""

from __future__ import annotations

import json
from typing import Any

import pytest

from autoswe.models import JobStatus
from autoswe.repository import JobRepository
from tests.conftest import ALICE, AppHarness, HarnessFactory


def parse_sse(raw: str) -> list[dict[str, Any]]:
    events = []
    for block in raw.split("\n\n"):
        fields: dict[str, str] = {}
        for line in block.splitlines():
            if line.startswith(":") or ":" not in line:
                continue
            key, _, value = line.partition(":")
            fields[key] = value.strip()
        if "event" in fields:
            events.append(
                {"id": fields.get("id"), "event": fields["event"], **json.loads(fields["data"])}
            )
    return events


async def test_stream_replays_full_history_then_ends(harness: AppHarness) -> None:
    job_id = (await harness.submit())["id"]
    await harness.wait_for(job_id, {"succeeded"})

    response = await harness.client.get(f"/api/v1/jobs/{job_id}/events", headers=ALICE)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["cache-control"] == "no-cache"
    events = parse_sse(response.text)

    types = [e["event"] for e in events]
    assert types[0] == "job.queued"
    assert types[1] == "job.started"
    assert types[-2:] == ["job.succeeded", "end"]
    started = [e["stage"] for e in events if e["event"] == "stage.started"]
    completed = [e["stage"] for e in events if e["event"] == "stage.completed"]
    assert started == completed == ["analyze", "architect", "implement", "devops", "review"]
    assert all(e["event"] != "stage.log" or e["message"] for e in events)
    ids = [int(e["id"]) for e in events if e["id"]]
    assert ids == sorted(ids)
    assert events[-1]["status"] == "succeeded"


async def test_stream_resumes_after_last_event_id(harness: AppHarness) -> None:
    job_id = (await harness.submit())["id"]
    await harness.wait_for(job_id, {"succeeded"})
    url = f"/api/v1/jobs/{job_id}/events"
    everything = parse_sse((await harness.client.get(url, headers=ALICE)).text)
    pivot = everything[5]["id"]

    via_header = parse_sse(
        (await harness.client.get(url, headers={**ALICE, "Last-Event-ID": pivot})).text
    )
    via_query = parse_sse((await harness.client.get(f"{url}?after={pivot}", headers=ALICE)).text)
    assert via_header == via_query == everything[6:]


async def test_stream_for_cancelled_queued_job(harness_factory: HarnessFactory) -> None:
    harness = await harness_factory(worker_enabled=False)
    job_id = (await harness.submit())["id"]
    await harness.client.post(f"/api/v1/jobs/{job_id}/cancel", headers=ALICE)
    events = parse_sse(
        (await harness.client.get(f"/api/v1/jobs/{job_id}/events", headers=ALICE)).text
    )
    assert [e["event"] for e in events] == ["job.queued", "job.cancelled", "end"]


async def test_live_stream_never_ends_before_the_terminal_event(
    harness_factory: HarnessFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression: the job used to turn terminal one commit before its terminal event, so a
    stream polling in between sent ``end`` without ``job.cancelled``/``job.succeeded``.

    The job is finished at the worst moment: inside the stream's second status read.
    """
    harness = await harness_factory(worker_enabled=False)
    job_id = (await harness.submit())["id"]
    orchestrator = harness.app.state.orchestrator
    real_status_of = JobRepository.status_of
    calls = 0

    async def status_of(self: JobRepository, job: str) -> JobStatus | None:
        nonlocal calls
        calls += 1
        if calls == 2:
            await orchestrator._cancelled(job_id)
        return await real_status_of(self, job)

    monkeypatch.setattr(JobRepository, "status_of", status_of)
    events = parse_sse(
        (await harness.client.get(f"/api/v1/jobs/{job_id}/events", headers=ALICE)).text
    )
    assert [e["event"] for e in events] == ["job.queued", "job.cancelled", "end"]
