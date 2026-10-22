"""Job endpoints: submit, inspect, stream progress, browse and download the generated project."""

from __future__ import annotations

import asyncio
import json
import re
import time
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Header, HTTPException, Query, Request, Response, status
from fastapi.responses import StreamingResponse

from autoswe.api.deps import PrincipalDep, RepositoryDep, ServicesDep
from autoswe.llm.registry import UnknownProviderError
from autoswe.models import Job, JobStatus
from autoswe.observability import JOBS_SUBMITTED
from autoswe.pipeline.artifacts import (
    ArchitectureSpec,
    Requirements,
    ReviewReport,
    ValidationReport,
)
from autoswe.pipeline.naming import kebab
from autoswe.pipeline.packaging import build_zip
from autoswe.repository import JobRepository
from autoswe.schemas import (
    FileContent,
    FileEntry,
    JobCreate,
    JobDetail,
    JobPage,
    JobSummary,
    language_for,
)

router = APIRouter(prefix="/api/v1/jobs", tags=["jobs"])

SSE_KEEPALIVE_S = 15.0
SSE_POLL_S = 1.0


def _title_from_spec(spec: str, limit: int = 80) -> str:
    """First sentence of the spec, shortened at a word boundary."""
    first = spec.strip().splitlines()[0]
    sentence = re.split(r"(?<=[.!?:])\s", first, maxsplit=1)[0].rstrip(".:")
    if len(sentence) <= limit:
        return sentence
    cut = sentence[: limit - 1].rsplit(" ", 1)[0].rstrip(",;:-")
    return cut + "…"  # ellipsis


async def _owned_job(repo: JobRepository, job_id: str, owner: str) -> Job:
    job = await repo.get(job_id, owner=owner)
    if job is None:  # 404 (not 403) so job IDs of other principals are not disclosed
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Job not found")
    return job


@router.post("", status_code=status.HTTP_202_ACCEPTED, response_model=JobDetail)
async def create_job(
    payload: JobCreate, principal: PrincipalDep, repo: RepositoryDep, services: ServicesDep
) -> JobDetail:
    try:
        provider = services.registry.get(payload.provider)
    except UnknownProviderError:
        available = [p.name for p in services.registry.describe()]
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Unknown provider '{payload.provider}'. Available: {available}",
        ) from None
    limit = services.settings.max_active_jobs_per_owner
    if await repo.count_active(principal.name) >= limit:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"At most {limit} queued or running jobs per API key.",
        )
    job = await repo.create(
        owner=principal.name,
        title=payload.title or _title_from_spec(payload.spec),
        spec=payload.spec,
        provider=provider.name,
        model=provider.model,
    )
    JOBS_SUBMITTED.labels(provider.name).inc()
    services.notifier.notify_queue()
    refreshed = await repo.get(job.id)
    assert refreshed is not None
    return JobDetail.from_model(refreshed)


@router.get("", response_model=JobPage)
async def list_jobs(
    principal: PrincipalDep,
    repo: RepositoryDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    before: Annotated[datetime | None, Query(description="Cursor from next_cursor")] = None,
) -> JobPage:
    jobs = await repo.list_for_owner(principal.name, limit=limit + 1, before=before)
    page = list(jobs[:limit])
    next_cursor = page[-1].created_at if len(jobs) > limit else None
    return JobPage(items=[JobSummary.from_model(job) for job in page], next_cursor=next_cursor)


@router.get("/{job_id}", response_model=JobDetail)
async def get_job(job_id: str, principal: PrincipalDep, repo: RepositoryDep) -> JobDetail:
    return JobDetail.from_model(await _owned_job(repo, job_id, principal.name))


@router.post("/{job_id}/cancel", response_model=JobDetail)
async def cancel_job(
    job_id: str, principal: PrincipalDep, repo: RepositoryDep, services: ServicesDep
) -> JobDetail:
    job = await _owned_job(repo, job_id, principal.name)
    if job.status.terminal:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=f"Job already {job.status.value}")
    outcome = await repo.request_cancel(job_id)
    if outcome == "cancel_requested" and services.worker is not None:
        services.worker.cancel_local(job_id)
    services.notifier.notify_job(job_id)
    refreshed = await repo.get(job_id)
    assert refreshed is not None
    if outcome is None:  # it finished between the read above and the cancel
        raise HTTPException(
            status.HTTP_409_CONFLICT, detail=f"Job already {refreshed.status.value}"
        )
    return JobDetail.from_model(refreshed)


@router.delete("/{job_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_job(job_id: str, principal: PrincipalDep, repo: RepositoryDep) -> Response:
    job = await _owned_job(repo, job_id, principal.name)
    if not job.status.terminal:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="Cancel the job before deleting it")
    await repo.delete(job)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/{job_id}/requirements", response_model=Requirements)
async def get_requirements(job_id: str, principal: PrincipalDep, repo: RepositoryDep) -> Any:
    job = await _owned_job(repo, job_id, principal.name)
    if job.requirements is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Requirements not produced yet")
    return job.requirements


@router.get("/{job_id}/architecture", response_model=ArchitectureSpec)
async def get_architecture(job_id: str, principal: PrincipalDep, repo: RepositoryDep) -> Any:
    job = await _owned_job(repo, job_id, principal.name)
    if job.architecture is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Architecture not produced yet")
    return job.architecture


class _ReviewOut(ReviewReport):
    validation: ValidationReport


@router.get("/{job_id}/review", response_model=_ReviewOut)
async def get_review(job_id: str, principal: PrincipalDep, repo: RepositoryDep) -> Any:
    job = await _owned_job(repo, job_id, principal.name)
    if job.review is None or job.validation is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Review not produced yet")
    return {**job.review, "validation": job.validation}


@router.get("/{job_id}/files", response_model=list[FileEntry])
async def list_files(job_id: str, principal: PrincipalDep, repo: RepositoryDep) -> list[FileEntry]:
    await _owned_job(repo, job_id, principal.name)
    return [
        FileEntry(path=path, size=size, stage=stage, description=description,
                  language=language_for(path))
        for path, size, stage, description in await repo.list_file_meta(job_id)
    ]  # fmt: skip


@router.get("/{job_id}/files/{path:path}", response_model=FileContent)
async def get_file(
    job_id: str, path: str, principal: PrincipalDep, repo: RepositoryDep
) -> FileContent:
    await _owned_job(repo, job_id, principal.name)
    file = await repo.get_file(job_id, path)
    if file is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="File not found")
    return FileContent(
        path=file.path, size=file.size, stage=file.stage, description=file.description,
        language=language_for(file.path), content=file.content,
    )  # fmt: skip


@router.get(
    "/{job_id}/archive",
    response_class=Response,
    responses={200: {"content": {"application/zip": {}}}},
)
async def download_archive(job_id: str, principal: PrincipalDep, repo: RepositoryDep) -> Response:
    job = await _owned_job(repo, job_id, principal.name)
    if job.status is not JobStatus.SUCCEEDED:
        raise HTTPException(
            status.HTTP_409_CONFLICT, detail="Archive is available once the job succeeds"
        )
    files = await repo.list_files(job_id)
    # project_name comes from the model: kebab() keeps the header to ASCII letters, digits and
    # hyphens (a quote, CR/LF or non-Latin-1 character would break or fail the header), and it
    # matches the folder name inside the archive.
    name = kebab(job.project_name or "project")
    archive = await asyncio.to_thread(build_zip, name, [(f.path, f.content) for f in files])
    return Response(
        content=archive,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{name}.zip"'},
    )


def _sse(event_id: int | None, event: str, data: dict[str, Any]) -> str:
    lines = []
    if event_id is not None:
        lines.append(f"id: {event_id}")
    lines.append(f"event: {event}")
    lines.append(f"data: {json.dumps(data, default=_json_default)}")
    return "\n".join(lines) + "\n\n"


def _json_default(value: object) -> str:
    # ISO 8601, like every timestamp in the REST responses: str() puts a space before the time,
    # which Safari's Date parser rejects.
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _event_cursor(last_event_id: str | None, after: int | None) -> int:
    """Resume position: ``Last-Event-ID`` wins over ``?after=``; an unparsable header is ignored."""
    value = (last_event_id or "").strip()
    # str.isdigit() also accepts characters such as "²" that int() rejects: require ASCII 0-9.
    if value.isascii() and value.isdecimal():
        return int(value)
    return after or 0


@router.get(
    "/{job_id}/events",
    response_class=StreamingResponse,
    responses={200: {"content": {"text/event-stream": {}}}},
)
async def stream_events(
    job_id: str,
    request: Request,
    principal: PrincipalDep,
    repo: RepositoryDep,
    services: ServicesDep,
    last_event_id: Annotated[str | None, Header()] = None,
    after: Annotated[int | None, Query(ge=0, description="Resume after this event id")] = None,
) -> StreamingResponse:
    """Server-Sent Events: replays the persisted event log, then follows it live.

    Reconnecting clients send ``Last-Event-ID`` (or ``?after=``) and miss nothing. The stream
    ends with an ``end`` event once the job is terminal and fully replayed.
    """
    await _owned_job(repo, job_id, principal.name)
    # Release the request-scoped connection now; the stream uses short sessions per poll so a
    # long-lived SSE client never pins a pooled connection.
    await repo.session.close()
    cursor = _event_cursor(last_event_id, after)

    async def generate() -> AsyncIterator[str]:
        nonlocal cursor
        yield "retry: 2000\n\n"
        last_sent = time.monotonic()
        while True:
            async with services.db.session() as session:
                stream_repo = JobRepository(session)
                # Status first, then events: a job seen as terminal here already has its
                # terminal event committed (JobRepository.finish), so this read includes it.
                job_status = await stream_repo.status_of(job_id)
                events = await stream_repo.events_after(job_id, cursor)
            for event in events:
                cursor = event.id
                payload = {"type": event.type, "at": event.created_at, **event.payload}
                yield _sse(event.id, event.type, payload)
                last_sent = time.monotonic()
            if job_status is None or (job_status.terminal and not events):
                yield _sse(None, "end", {"status": job_status.value if job_status else "deleted"})
                return
            if await request.is_disconnected():
                return
            if time.monotonic() - last_sent > SSE_KEEPALIVE_S:
                yield ": keep-alive\n\n"
                last_sent = time.monotonic()
            if not events:
                await services.notifier.wait_for_job(job_id, SSE_POLL_S)

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
