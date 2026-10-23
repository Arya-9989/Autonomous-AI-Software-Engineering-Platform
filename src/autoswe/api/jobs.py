"""Job endpoints: submit, inspect, stream progress, browse and download the generated project."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import time
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Header, HTTPException, Query, Request, Response, status
from fastapi.responses import StreamingResponse

from autoswe.api.deps import PrincipalDep, RepositoryDep, ServicesDep
from autoswe.budget import TokenBudgetExceededError, UsageRepository, period_bounds
from autoswe.llm.base import is_metered
from autoswe.llm.registry import UnknownProviderError
from autoswe.logging_config import request_id_var
from autoswe.models import Job, JobStatus
from autoswe.observability import (
    BUDGET_REJECTIONS,
    GITHUB_PUBLISH,
    JOB_RETRIES,
    JOBS_SUBMITTED,
)
from autoswe.pipeline.artifacts import (
    ArchitectureSpec,
    GeneratedFile,
    Requirements,
    ReviewReport,
    ValidationReport,
)
from autoswe.pipeline.naming import kebab
from autoswe.pipeline.packaging import build_zip
from autoswe.publishing import (
    GitHubClient,
    PublishError,
    publish_project,
    pull_request_body,
    repository_name_problem,
)
from autoswe.repository import (
    BudgetRefusedError,
    DuplicateSubmissionError,
    JobRepository,
    Reservation,
)
from autoswe.schemas import (
    FileContent,
    FileEntry,
    JobCreate,
    JobDetail,
    JobPage,
    JobSummary,
    PublicationOut,
    PublishRequest,
    language_for,
)
from autoswe.tracing import current_context_carrier, mark_error, tracer

router = APIRouter(prefix="/api/v1/jobs", tags=["jobs"])
logger = logging.getLogger(__name__)

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


IdempotencyKey = Annotated[
    str | None,
    Header(
        alias="Idempotency-Key",
        pattern=r"^[A-Za-z0-9._:-]{1,128}$",
        description="Retrying a submission with the same key returns the job it created "
        "instead of creating (and paying for) another. Reusing a key with a different body is "
        "a 422. A key is held for as long as its job exists.",
    ),
]


def _request_hash(payload: JobCreate) -> str:
    body = json.dumps(payload.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(body.encode()).hexdigest()


def _replay(job: Job, request_hash: str, response: Response) -> JobDetail:
    if job.request_hash != request_hash:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Idempotency-Key was already used for a different request",
        )
    response.headers["Idempotent-Replayed"] = "true"
    return JobDetail.from_model(job)


@router.post(
    "",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=JobDetail,
    responses={422: {"description": "Invalid body, or an Idempotency-Key reused for another"}},
)
async def create_job(
    payload: JobCreate,
    principal: PrincipalDep,
    repo: RepositoryDep,
    services: ServicesDep,
    response: Response,
    idempotency_key: IdempotencyKey = None,
) -> JobDetail:
    request_hash = _request_hash(payload) if idempotency_key else None
    if idempotency_key and request_hash:
        existing = await repo.by_idempotency_key(principal.name, idempotency_key)
        if existing is not None:  # a retry: hand back the job, no new job, no new reservation
            return _replay(existing, request_hash, response)
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
    reservation = None
    if is_metered(provider):
        policy = services.budget
        budget = policy.limit_for(principal.name)
        start, _ = period_bounds(policy.period)
        reservation = Reservation(start, policy.reservation_for(budget), budget)
    try:
        job = await repo.create(
            owner=principal.name,
            title=payload.title or _title_from_spec(payload.spec),
            spec=payload.spec,
            provider=provider.name,
            model=provider.model,
            reservation=reservation,
            request_id=request_id_var.get(),
            trace_context=current_context_carrier(),
            stack=payload.stack.value,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
        )
    except DuplicateSubmissionError:  # a concurrent retry with the same key got there first
        assert idempotency_key is not None
        assert request_hash is not None
        winner = await repo.by_idempotency_key(principal.name, idempotency_key)
        assert winner is not None
        return _replay(winner, request_hash, response)
    except BudgetRefusedError:
        BUDGET_REJECTIONS.labels("submit").inc()
        state = await UsageRepository(repo.session).state(services.budget, principal.name)
        raise TokenBudgetExceededError(
            f"Token budget exhausted: {state.used} of {state.limit} tokens used and "
            f"{state.reserved} reserved by running jobs this {state.period}"
            + (f"; it resets at {state.resets_at.isoformat()}" if state.resets_at else ""),
            state,
        ) from None
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
    before_id: Annotated[str | None, Query(description="Cursor from next_cursor_id")] = None,
) -> JobPage:
    jobs = await repo.list_for_owner(
        principal.name, limit=limit + 1, before=before, before_id=before_id
    )
    page = list(jobs[:limit])
    more = len(jobs) > limit
    return JobPage(
        items=[JobSummary.from_model(job) for job in page],
        next_cursor=page[-1].created_at if more else None,
        next_cursor_id=page[-1].id if more else None,
    )


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


@router.post(
    "/{job_id}/retry",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=JobDetail,
    responses={402: {"description": "The token budget has no room for the job's reservation"},
               409: {"description": "Only a failed job can be retried"},
               429: {"description": "Too many queued or running jobs for this API key"}},
)  # fmt: skip
async def retry_job(
    job_id: str, principal: PrincipalDep, repo: RepositoryDep, services: ServicesDep
) -> JobDetail:
    """Requeue a failed job. It resumes at the stage that failed: the stages that completed
    keep their results (and were already paid for), so only the rest runs again."""
    job = await _owned_job(repo, job_id, principal.name)
    if job.status is not JobStatus.FAILED:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail=f"Only a failed job can be retried; it is {job.status.value}",
        )
    limit = services.settings.max_active_jobs_per_owner
    if await repo.count_active(principal.name) >= limit:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"At most {limit} queued or running jobs per API key.",
        )
    try:
        provider = services.registry.get(job.provider)
    except UnknownProviderError:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail=f"The job's provider '{job.provider}' is no longer configured",
        ) from None
    reservation = None
    if is_metered(provider):
        policy = services.budget
        budget = policy.limit_for(principal.name)
        start, _ = period_bounds(policy.period)
        reservation = Reservation(start, policy.reservation_for(budget), budget)
    try:
        retried = await repo.retry_failed(job_id, principal.name, reservation)
    except BudgetRefusedError:
        BUDGET_REJECTIONS.labels("retry").inc()
        state = await UsageRepository(repo.session).state(services.budget, principal.name)
        raise TokenBudgetExceededError(
            f"Token budget exhausted: {state.used} of {state.limit} tokens used and "
            f"{state.reserved} reserved by running jobs this {state.period}",
            state,
        ) from None
    if not retried:  # it changed state between the read above and the retry
        current = await repo.get(job_id)
        detail = current.status.value if current else "gone"
        raise HTTPException(
            status.HTTP_409_CONFLICT, detail=f"Only a failed job can be retried; it is {detail}"
        )
    JOB_RETRIES.labels("manual").inc()
    services.notifier.notify_job(job_id)
    services.notifier.notify_queue()
    refreshed = await repo.get(job_id)
    assert refreshed is not None
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


@router.get("/{job_id}/tests")
async def get_test_report(job_id: str, principal: PrincipalDep, repo: RepositoryDep) -> Any:
    """The sandboxed test run: the final result (lint, type check, tests, failures) and the
    status of every attempt, including the ones before each repair round."""
    job = await _owned_job(repo, job_id, principal.name)
    if job.test_report is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Tests were not run for this job")
    return job.test_report


@router.post(
    "/{job_id}/publish",
    status_code=status.HTTP_201_CREATED,
    response_model=PublicationOut,
    responses={409: {"description": "Not succeeded, already published, or the name is taken"},
               503: {"description": "Publishing is not configured, or GitHub rate-limited us"}},
)  # fmt: skip
async def publish_job(
    job_id: str,
    payload: PublishRequest,
    principal: PrincipalDep,
    repo: RepositoryDep,
    services: ServicesDep,
) -> Any:
    """Create a new GitHub repository, commit the project on a branch and open a pull request.

    Uses the server's publishing token (``GITHUB_PUBLISH_TOKEN``); the caller chooses only the
    new repository's name, visibility and description. A job is published at most once."""
    job = await _owned_job(repo, job_id, principal.name)
    settings = services.settings
    if not settings.github_publishing_enabled:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Publishing to GitHub is not configured (GITHUB_PUBLISH_TOKEN).",
        )
    problem = repository_name_problem(payload.repository)
    if problem:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, detail=f"Invalid repository name: {problem}"
        )
    if job.status is not JobStatus.SUCCEEDED:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="Only a succeeded job is published")
    if not await repo.claim_publication(job_id):
        current = (await repo.get(job_id)) or job
        where = (current.publication or {}).get("pull_request_url") or "in progress"
        raise HTTPException(status.HTTP_409_CONFLICT, detail=f"Job already published ({where})")
    files = [GeneratedFile(path=f.path, content=f.content) for f in await repo.list_files(job_id)]
    assert settings.github_publish_token is not None
    with tracer().start_as_current_span(
        "autoswe.github.publish",
        attributes={"autoswe.job.id": job_id, "autoswe.github.repository": payload.repository,
                    "autoswe.github.visibility": payload.visibility, "autoswe.files": len(files)},
    ) as span:  # fmt: skip
        try:
            async with GitHubClient(
                settings.github_publish_token.get_secret_value(),
                api_url=settings.github_api_url,
                timeout_s=settings.github_timeout_s,
                transport=services.github_transport,
            ) as client:
                publication = await publish_project(
                    client,
                    owner=settings.github_publish_owner,
                    name=payload.repository,
                    visibility=payload.visibility,
                    description=payload.description or job.title,
                    files=files,
                    branch=f"autoswe/{job_id[:8]}",
                    title=f"AutoSWE: {job.title}",
                    body=pull_request_body(job, len(files)),
                )
        except PublishError as exc:
            GITHUB_PUBLISH.labels(exc.code).inc()
            mark_error(span, exc, exc.code)
            message = str(exc)
            # A taken name or a failure before the repository existed can be retried later.
            created = "was created" in message
            failed = {"status": "failed", "error": message, "code": exc.code} if created else None
            await repo.record_publication(
                job_id, failed, "job.publish_failed", {"error": message, "code": exc.code}
            )
            logger.warning("publish failed", extra={"code": exc.code, "error": message})
            headers = {"Retry-After": exc.retry_after} if exc.retry_after else None
            raise HTTPException(exc.status_code, detail=message, headers=headers) from None
    GITHUB_PUBLISH.labels("published").inc()
    result = publication.as_dict()
    await repo.record_publication(job_id, result, "job.published", {
        "repository": publication.repository, "pull_request_url": publication.pull_request_url,
    })  # fmt: skip
    span.set_attribute("autoswe.github.pull_request", publication.pull_request_url)
    return result


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
