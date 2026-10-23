"""Data access for jobs. All queries are owner-scoped where a caller identity is involved."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import and_, delete, func, null, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from autoswe.budget import UsageRepository
from autoswe.models import (
    Job,
    JobEvent,
    JobFile,
    JobStatus,
    StageRun,
    StageStatus,
    utcnow,
)
from autoswe.pipeline.artifacts import STAGE_ORDER, GeneratedFile, StageName

ACTIVE_STATUSES = (JobStatus.QUEUED, JobStatus.RUNNING)


@dataclass(frozen=True, slots=True)
class Reservation:
    """The budget a metered job takes when it is created (see :mod:`autoswe.budget`)."""

    period_start: datetime
    amount: int
    limit: int | None


class BudgetRefusedError(Exception):
    """The reservation did not fit under the principal's remaining budget."""


def _rowcount(result: object) -> int:
    """Rows matched by an UPDATE (``CursorResult.rowcount``; typed loosely by SQLAlchemy)."""
    return int(getattr(result, "rowcount", 0) or 0)


class JobRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # --- API-facing ----------------------------------------------------------------------------

    async def create(
        self,
        *,
        owner: str,
        title: str,
        spec: str,
        provider: str,
        model: str,
        reservation: Reservation | None = None,
        request_id: str | None = None,
        trace_context: dict[str, str] | None = None,
        stack: str = "python",
    ) -> Job:
        """Insert a queued job. With a reservation, the budget compare-and-set runs in the
        same transaction, so a refused reservation leaves no job behind."""
        job = Job(owner=owner, title=title, spec=spec, provider=provider, model=model,
                  request_id=request_id, trace_context=trace_context or None,
                  stack=stack)  # fmt: skip
        if reservation is not None:
            usage = UsageRepository(self.session)
            await usage.ensure(owner, reservation.period_start)
            if reservation.limit is not None and not await usage.try_reserve(
                owner, reservation.period_start, reservation.amount, reservation.limit
            ):
                await self.session.rollback()
                raise BudgetRefusedError(owner)
            job.budget_period_start = reservation.period_start
            job.tokens_reserved = reservation.amount if reservation.limit is not None else 0
        job.stages = [
            StageRun(name=stage.value, position=index, status=StageStatus.PENDING)
            for index, stage in enumerate(STAGE_ORDER)
        ]
        self.session.add(job)
        await self.session.flush()
        self.session.add(JobEvent(job_id=job.id, type="job.queued", payload={"provider": provider}))
        await self.session.commit()
        return job

    async def get(self, job_id: str, owner: str | None = None) -> Job | None:
        # populate_existing: sessions keep objects after commit (expire_on_commit=False), so a
        # re-read after a guarded UPDATE must not hand back the stale identity-map copy.
        query = select(Job).where(Job.id == job_id).execution_options(populate_existing=True)
        if owner is not None:
            query = query.where(Job.owner == owner)
        return (await self.session.execute(query)).scalar_one_or_none()

    async def list_for_owner(
        self,
        owner: str,
        *,
        limit: int,
        before: datetime | None = None,
        before_id: str | None = None,
    ) -> Sequence[Job]:
        """Newest first. The cursor is (created_at, id), the same pair the list is ordered by,
        so jobs sharing a timestamp across a page boundary are neither skipped nor repeated."""
        query = select(Job).where(Job.owner == owner)
        if before is not None and before_id is not None:
            query = query.where(
                or_(Job.created_at < before, and_(Job.created_at == before, Job.id < before_id))
            )
        elif before is not None:
            query = query.where(Job.created_at < before)
        query = query.order_by(Job.created_at.desc(), Job.id.desc()).limit(limit)
        return (await self.session.execute(query)).scalars().all()

    async def count_active(self, owner: str) -> int:
        query = select(func.count()).where(Job.owner == owner, Job.status.in_(ACTIVE_STATUSES))
        return int((await self.session.execute(query)).scalar_one())

    async def count_queued(self) -> int:
        query = select(func.count()).where(Job.status == JobStatus.QUEUED)
        return int((await self.session.execute(query)).scalar_one())

    async def request_cancel(self, job_id: str) -> str | None:
        """Cancel a queued job, or flag a running one; returns what happened.

        Both branches are compare-and-set updates on the current status, so a cancel that races a
        worker's claim cannot overwrite ``running`` with ``cancelled`` (the claimed job would then
        run anyway, or be requeued by the interrupt handler). Whichever update matches decides:
        ``"cancelled"`` (it was still queued), ``"cancel_requested"`` (it is running; the worker
        stops at the next stage boundary) or ``None`` (it already finished).
        """
        now = utcnow()
        queued = await self.session.execute(
            update(Job)
            .where(Job.id == job_id, Job.status == JobStatus.QUEUED)
            .values(status=JobStatus.CANCELLED, finished_at=now, updated_at=now)
            .execution_options(synchronize_session=False)
        )
        if _rowcount(queued) == 1:
            await self._release(job_id)
            await self.session.execute(
                update(StageRun).where(StageRun.job_id == job_id).values(status=StageStatus.SKIPPED)
            )
            self.session.add(JobEvent(job_id=job_id, type="job.cancelled", payload={}))
            await self.session.commit()
            return "cancelled"
        running = await self.session.execute(
            update(Job)
            .where(
                Job.id == job_id,
                Job.status == JobStatus.RUNNING,
                Job.cancel_requested.is_(False),
            )
            .values(cancel_requested=True, updated_at=now)
            .execution_options(synchronize_session=False)
        )
        if _rowcount(running) == 1:
            self.session.add(JobEvent(job_id=job_id, type="job.cancel_requested", payload={}))
            await self.session.commit()
            return "cancel_requested"
        await self.session.commit()
        status = await self.status_of(job_id)
        return "cancel_requested" if status is JobStatus.RUNNING else None

    async def claim_publication(self, job_id: str) -> bool:
        """Mark a job as being published; only one request can win (compare-and-set)."""
        result = await self.session.execute(
            update(Job)
            .where(Job.id == job_id, Job.status == JobStatus.SUCCEEDED, Job.publication.is_(None))
            .values(publication={"status": "publishing"}, updated_at=utcnow())
            .execution_options(synchronize_session=False)
        )
        await self.session.commit()
        return _rowcount(result) == 1

    async def record_publication(
        self, job_id: str, publication: dict[str, Any] | None, event: str, payload: dict[str, Any]
    ) -> None:
        # SQL NULL, not JSON null, so claim_publication's "IS NULL" can match again.
        value = publication if publication is not None else null()
        await self.session.execute(
            update(Job).where(Job.id == job_id).values(publication=value, updated_at=utcnow())
        )
        self.session.add(JobEvent(job_id=job_id, type=event, payload=payload))
        await self.session.commit()

    async def delete(self, job: Job) -> None:
        await self.session.delete(job)
        await self.session.commit()

    async def list_files(self, job_id: str) -> Sequence[JobFile]:
        query = select(JobFile).where(JobFile.job_id == job_id).order_by(JobFile.path)
        return (await self.session.execute(query)).scalars().all()

    async def list_file_meta(self, job_id: str) -> Sequence[tuple[str, int, str, str]]:
        """Path, size, producing stage and description, without loading file contents."""
        query = (
            select(JobFile.path, JobFile.size, JobFile.stage, JobFile.description)
            .where(JobFile.job_id == job_id)
            .order_by(JobFile.path)
        )
        return [tuple(row) for row in (await self.session.execute(query)).all()]  # type: ignore[misc]

    async def discard_files(self, job_id: str, *, keep: set[str]) -> None:
        """Delete the job's files produced by any stage outside ``keep``."""
        await self.session.execute(
            delete(JobFile).where(JobFile.job_id == job_id, JobFile.stage.not_in(keep))
        )
        await self.session.commit()

    async def get_file(self, job_id: str, path: str) -> JobFile | None:
        query = select(JobFile).where(JobFile.job_id == job_id, JobFile.path == path)
        return (await self.session.execute(query)).scalar_one_or_none()

    async def events_after(
        self, job_id: str, after_id: int, limit: int = 200
    ) -> Sequence[JobEvent]:
        query = (
            select(JobEvent)
            .where(JobEvent.job_id == job_id, JobEvent.id > after_id)
            .order_by(JobEvent.id)
            .limit(limit)
        )
        return (await self.session.execute(query)).scalars().all()

    async def status_of(self, job_id: str) -> JobStatus | None:
        query = select(Job.status).where(Job.id == job_id)
        return (await self.session.execute(query)).scalar_one_or_none()

    # --- worker-facing -------------------------------------------------------------------------

    async def claim_next(self, worker_id: str) -> str | None:
        """Atomically move the oldest queued job to ``running``.

        The compare-and-set ``UPDATE ... WHERE status = 'queued'`` means two workers (or two
        replicas) racing for the same row cannot both win, on SQLite and PostgreSQL alike.
        """
        now = utcnow()
        due = or_(Job.run_after.is_(None), Job.run_after <= now)
        candidate = (
            await self.session.execute(
                select(Job.id)
                .where(Job.status == JobStatus.QUEUED, due)
                .order_by(Job.created_at, Job.id)
                .limit(1)
            )
        ).scalar_one_or_none()
        if candidate is None:
            return None
        result = await self.session.execute(
            update(Job)
            .where(Job.id == candidate, Job.status == JobStatus.QUEUED, due)
            .values(status=JobStatus.RUNNING, worker_id=worker_id, started_at=now,
                    heartbeat_at=now, updated_at=now)
            .execution_options(synchronize_session=False)
        )  # fmt: skip
        await self.session.commit()
        return candidate if _rowcount(result) == 1 else None

    async def heartbeat(self, job_id: str) -> None:
        await self.session.execute(
            update(Job).where(Job.id == job_id).values(heartbeat_at=utcnow())
        )
        await self.session.commit()

    async def cancel_requested(self, job_id: str) -> bool:
        query = select(Job.cancel_requested).where(Job.id == job_id)
        return bool((await self.session.execute(query)).scalar_one_or_none())

    async def add_event(self, job_id: str, type_: str, payload: dict[str, Any]) -> JobEvent:
        event = JobEvent(job_id=job_id, type=type_, payload=payload)
        self.session.add(event)
        await self.session.commit()
        return event

    async def finish(
        self, job_id: str, event_type: str, payload: dict[str, Any], **values: Any
    ) -> bool:
        """Move a running job to a terminal status and record its event in one transaction.

        An event stream that sees the terminal status has therefore always been able to see
        the terminal event too, so it never ends before sending it. The update only applies to
        a job that is still ``running``: a job the reaper already failed (lost heartbeat) keeps
        its outcome and gets no second terminal event. Returns whether the job was finished.
        """
        result = await self.session.execute(
            update(Job)
            .where(Job.id == job_id, Job.status == JobStatus.RUNNING)
            .values(updated_at=utcnow(), **values)
            .execution_options(synchronize_session=False)
        )
        if _rowcount(result) != 1:
            await self.session.rollback()
            return False
        await self._release(job_id)
        self.session.add(JobEvent(job_id=job_id, type=event_type, payload=payload))
        await self.session.commit()
        return True

    async def _release(self, job_id: str) -> None:
        await UsageRepository(self.session).release(job_id)

    async def update_job(self, job_id: str, **values: Any) -> None:
        await self.session.execute(
            update(Job).where(Job.id == job_id).values(updated_at=utcnow(), **values)
        )
        await self.session.commit()

    async def update_stage(self, job_id: str, stage: str, **values: Any) -> None:
        await self.session.execute(
            update(StageRun)
            .where(StageRun.job_id == job_id, StageRun.name == stage)
            .values(**values)
        )
        await self.session.commit()

    async def skip_pending_stages(self, job_id: str) -> None:
        await self.session.execute(
            update(StageRun)
            .where(
                StageRun.job_id == job_id,
                StageRun.status.in_((StageStatus.PENDING, StageStatus.RUNNING)),
            )
            .values(status=StageStatus.SKIPPED)
        )
        await self.session.commit()

    async def upsert_files(self, job_id: str, files: Iterable[GeneratedFile], stage: str) -> None:
        batch = list(files)
        if not batch:
            return
        paths = [file.path for file in batch]
        await self.session.execute(
            delete(JobFile).where(JobFile.job_id == job_id, JobFile.path.in_(paths))
        )
        self.session.add_all(
            JobFile(
                job_id=job_id,
                path=file.path,
                content=file.content,
                size=len(file.content.encode()),
                stage=stage,
                description=file.description,
            )
            for file in batch
        )
        await self.session.commit()

    async def _requeue(self, job_id: str, from_status: JobStatus, **values: Any) -> bool:
        """Move a ``from_status`` job back to ``queued``, keeping every stage that succeeded.

        Completed stages keep their summary, usage, files and artifacts, so the next attempt
        resumes at the first stage that did not finish instead of paying for the whole pipeline
        again. Job-level token and cost totals are kept too: earlier attempts were billed.
        Does not commit; returns whether the compare-and-set on the status won.
        """
        moved = await self.session.execute(
            update(Job)
            .where(Job.id == job_id, Job.status == from_status)
            .values(status=JobStatus.QUEUED, current_stage=None, worker_id=None, started_at=None,
                    heartbeat_at=None, updated_at=utcnow(), **values)
            .execution_options(synchronize_session=False)
        )  # fmt: skip
        if _rowcount(moved) != 1:
            return False
        done = list(
            (
                await self.session.execute(
                    select(StageRun.name).where(
                        StageRun.job_id == job_id, StageRun.status == StageStatus.SUCCEEDED
                    )
                )
            ).scalars()
        )
        await self.session.execute(
            delete(JobFile).where(JobFile.job_id == job_id, JobFile.stage.not_in(done))
        )
        await self.session.execute(
            update(StageRun)
            .where(StageRun.job_id == job_id, StageRun.status != StageStatus.SUCCEEDED)
            .values(status=StageStatus.PENDING, summary=None, started_at=None, finished_at=None,
                    input_tokens=0, output_tokens=0, cache_read_input_tokens=0,
                    cache_creation_input_tokens=0, cost_microusd=0)
        )  # fmt: skip
        # Artifacts of stages that did not finish (the review's always: it is the last stage).
        cleared: dict[str, Any] = {"validation": None, "review": None, "test_report": None}
        if StageName.ANALYZE.value not in done:
            cleared |= {"requirements": None, "project_name": None}
        if StageName.ARCHITECT.value not in done:
            cleared["architecture"] = None
        await self.session.execute(update(Job).where(Job.id == job_id).values(**cleared))
        return True

    async def reset_for_retry(self, job_id: str) -> bool:
        """Put an interrupted job (worker shutdown) back in the queue, keeping its checkpoints.

        Only a job that is still ``running`` is requeued; one that was reaped or finished in the
        meantime keeps its outcome. The job keeps its budget reservation while it waits.
        """
        if not await self._requeue(job_id, JobStatus.RUNNING, run_after=None):
            await self.session.rollback()
            return False
        self.session.add(JobEvent(job_id=job_id, type="job.requeued", payload={}))
        await self.session.commit()
        return True

    async def schedule_retry(
        self, job_id: str, *, stage: str, delay_s: float, error: str, code: str | None = None
    ) -> int | None:
        """Requeue a running job after a transient provider failure in ``stage``.

        It becomes claimable again ``delay_s`` from now and resumes at ``stage``; it keeps its
        budget reservation. Returns the retry number, or None if the job had already left
        ``running`` (reaped or finished), in which case nothing changes.
        """
        run_after = utcnow() + timedelta(seconds=delay_s)
        if not await self._requeue(
            job_id, JobStatus.RUNNING, retries=Job.retries + 1, run_after=run_after
        ):
            await self.session.rollback()
            return None
        retries = (
            await self.session.execute(select(Job.retries).where(Job.id == job_id))
        ).scalar_one()
        payload = {"stage": stage, "attempt": retries, "delay_s": round(delay_s, 1),
                   "error": error, "code": code, "run_after": run_after.isoformat()}  # fmt: skip
        self.session.add(JobEvent(job_id=job_id, type="job.retry_scheduled", payload=payload))
        await self.session.commit()
        return int(retries)

    async def retry_failed(
        self, job_id: str, owner: str, reservation: Reservation | None = None
    ) -> bool:
        """Requeue the owner's failed job, resuming at the stage that failed.

        Its budget reservation was released when it failed, so a fresh one is taken in the same
        transaction (BudgetRefusedError if it no longer fits). The automatic retry count starts
        again from zero. Returns False when the job is not ``failed`` (or not the owner's).
        """
        owned = await self.session.execute(
            select(Job.id).where(Job.id == job_id, Job.owner == owner)
        )
        if owned.scalar_one_or_none() is None:
            return False
        values: dict[str, Any] = {"error": None, "error_code": None, "finished_at": None,
                                  "retries": 0, "run_after": None}  # fmt: skip
        if reservation is not None:
            values["budget_period_start"] = reservation.period_start
            values["tokens_reserved"] = reservation.amount if reservation.limit is not None else 0
        if not await self._requeue(job_id, JobStatus.FAILED, **values):
            await self.session.rollback()
            return False
        if reservation is not None:
            usage = UsageRepository(self.session)
            await usage.ensure(owner, reservation.period_start)
            if reservation.limit is not None and not await usage.try_reserve(
                owner, reservation.period_start, reservation.amount, reservation.limit
            ):
                await self.session.rollback()
                raise BudgetRefusedError(owner)
        self.session.add(JobEvent(job_id=job_id, type="job.retried", payload={}))
        await self.session.commit()
        return True

    async def fail_stale(self, older_than: timedelta) -> list[str]:
        """Fail running jobs whose worker stopped heart-beating (crash, OOM kill, lost node)."""
        cutoff = utcnow() - older_than
        stale = (
            (
                await self.session.execute(
                    select(Job.id).where(
                        Job.status == JobStatus.RUNNING,
                        func.coalesce(Job.heartbeat_at, Job.started_at, Job.created_at) < cutoff,
                    )
                )
            )
            .scalars()
            .all()
        )
        for job_id in stale:
            reaped = await self.session.execute(
                update(Job)
                .where(Job.id == job_id, Job.status == JobStatus.RUNNING)
                .values(status=JobStatus.FAILED, error="Worker heartbeat lost; job abandoned.",
                        finished_at=utcnow(), updated_at=utcnow())
            )  # fmt: skip
            if _rowcount(reaped) == 1:
                await self._release(job_id)
            self.session.add(
                JobEvent(job_id=job_id, type="job.failed",
                         payload={"error": "Worker heartbeat lost; job abandoned."})
            )  # fmt: skip
        await self.session.commit()
        if stale:
            for job_id in stale:
                await self.skip_pending_stages(job_id)
        return list(stale)
