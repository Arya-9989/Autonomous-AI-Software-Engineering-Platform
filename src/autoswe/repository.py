"""Data access for jobs. All queries are owner-scoped where a caller identity is involved."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from autoswe.models import (
    Job,
    JobEvent,
    JobFile,
    JobStatus,
    StageRun,
    StageStatus,
    utcnow,
)
from autoswe.pipeline.artifacts import STAGE_ORDER, GeneratedFile

ACTIVE_STATUSES = (JobStatus.QUEUED, JobStatus.RUNNING)


class JobRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # --- API-facing ----------------------------------------------------------------------------

    async def create(self, *, owner: str, title: str, spec: str, provider: str, model: str) -> Job:
        job = Job(owner=owner, title=title, spec=spec, provider=provider, model=model)
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
        query = select(Job).where(Job.id == job_id)
        if owner is not None:
            query = query.where(Job.owner == owner)
        return (await self.session.execute(query)).scalar_one_or_none()

    async def list_for_owner(
        self, owner: str, *, limit: int, before: datetime | None = None
    ) -> Sequence[Job]:
        query = select(Job).where(Job.owner == owner)
        if before is not None:
            query = query.where(Job.created_at < before)
        query = query.order_by(Job.created_at.desc(), Job.id.desc()).limit(limit)
        return (await self.session.execute(query)).scalars().all()

    async def count_active(self, owner: str) -> int:
        query = select(func.count()).where(Job.owner == owner, Job.status.in_(ACTIVE_STATUSES))
        return int((await self.session.execute(query)).scalar_one())

    async def count_queued(self) -> int:
        query = select(func.count()).where(Job.status == JobStatus.QUEUED)
        return int((await self.session.execute(query)).scalar_one())

    async def request_cancel(self, job: Job) -> Job:
        if job.status is JobStatus.QUEUED:
            job.status = JobStatus.CANCELLED
            job.finished_at = utcnow()
            for stage in job.stages:
                stage.status = StageStatus.SKIPPED
            self.session.add(JobEvent(job_id=job.id, type="job.cancelled", payload={}))
        elif job.status is JobStatus.RUNNING:
            job.cancel_requested = True
            self.session.add(JobEvent(job_id=job.id, type="job.cancel_requested", payload={}))
        await self.session.commit()
        return job

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
        candidate = (
            await self.session.execute(
                select(Job.id)
                .where(Job.status == JobStatus.QUEUED)
                .order_by(Job.created_at, Job.id)
                .limit(1)
            )
        ).scalar_one_or_none()
        if candidate is None:
            return None
        now = utcnow()
        result = await self.session.execute(
            update(Job)
            .where(Job.id == candidate, Job.status == JobStatus.QUEUED)
            .values(status=JobStatus.RUNNING, worker_id=worker_id, started_at=now,
                    heartbeat_at=now, updated_at=now)
            .execution_options(synchronize_session=False)
        )  # fmt: skip
        await self.session.commit()
        return candidate if getattr(result, "rowcount", 0) == 1 else None

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

    async def reset_for_retry(self, job_id: str) -> None:
        """Put an interrupted job back in the queue with a clean slate."""
        await self.session.execute(delete(JobFile).where(JobFile.job_id == job_id))
        await self.session.execute(
            update(StageRun)
            .where(StageRun.job_id == job_id)
            .values(status=StageStatus.PENDING, summary=None, started_at=None, finished_at=None,
                    input_tokens=0, output_tokens=0)
        )  # fmt: skip
        await self.session.execute(
            update(Job)
            .where(Job.id == job_id)
            .values(status=JobStatus.QUEUED, current_stage=None, worker_id=None, started_at=None,
                    heartbeat_at=None, input_tokens=0, output_tokens=0, requirements=None,
                    architecture=None, validation=None, review=None, updated_at=utcnow())
        )  # fmt: skip
        self.session.add(JobEvent(job_id=job_id, type="job.requeued", payload={}))
        await self.session.commit()

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
            await self.session.execute(
                update(Job)
                .where(Job.id == job_id, Job.status == JobStatus.RUNNING)
                .values(status=JobStatus.FAILED, error="Worker heartbeat lost; job abandoned.",
                        finished_at=utcnow(), updated_at=utcnow())
            )  # fmt: skip
            self.session.add(
                JobEvent(job_id=job_id, type="job.failed",
                         payload={"error": "Worker heartbeat lost; job abandoned."})
            )  # fmt: skip
        await self.session.commit()
        if stale:
            for job_id in stale:
                await self.skip_pending_stages(job_id)
        return list(stale)
