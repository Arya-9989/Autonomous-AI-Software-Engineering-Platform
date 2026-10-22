"""Runs one job through the agent pipeline, persisting progress after every step.

Every state transition is committed to the database and recorded as a ``JobEvent`` before the
in-process notifier is poked, so SSE clients (on any replica) can replay a job's history from
the event log and the UI never shows progress that was not durably stored.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import Sequence
from typing import Any

from autoswe.db import Database
from autoswe.events import JobNotifier
from autoswe.llm.base import LLMError, LLMProvider
from autoswe.llm.registry import ProviderRegistry
from autoswe.logging_config import job_id_var
from autoswe.models import JobStatus, StageStatus, utcnow
from autoswe.observability import (
    JOBS_FINISHED,
    JOBS_RUNNING,
    LLM_TOKENS,
    STAGE_DURATION,
    VALIDATION_ISSUES,
)
from autoswe.pipeline.agents import Agent, PipelineState, StageResult, default_agents
from autoswe.pipeline.artifacts import STAGE_LABELS, StageName
from autoswe.pipeline.validation import Limits
from autoswe.repository import JobRepository

logger = logging.getLogger(__name__)

HEARTBEAT_INTERVAL_S = 10.0


class JobCancelledError(Exception):
    pass


class Orchestrator:
    def __init__(
        self,
        db: Database,
        registry: ProviderRegistry,
        notifier: JobNotifier,
        *,
        limits: Limits,
        max_repair_rounds: int = 1,
        agents: Sequence[Agent] | None = None,
    ) -> None:
        self._db = db
        self._registry = registry
        self._notifier = notifier
        self._limits = limits
        self._max_repair_rounds = max_repair_rounds
        self._agents = list(agents) if agents is not None else default_agents()

    # --- persistence helpers ----------------------------------------------------------------------

    async def _emit(self, job_id: str, type_: str, **payload: Any) -> None:
        async with self._db.session() as session:
            await JobRepository(session).add_event(job_id, type_, payload)
        self._notifier.notify_job(job_id)

    async def _update_job(self, job_id: str, **values: Any) -> None:
        async with self._db.session() as session:
            await JobRepository(session).update_job(job_id, **values)

    async def _update_stage(self, job_id: str, stage: StageName, **values: Any) -> None:
        async with self._db.session() as session:
            await JobRepository(session).update_stage(job_id, stage.value, **values)

    async def _cancel_requested(self, job_id: str) -> bool:
        async with self._db.session() as session:
            return await JobRepository(session).cancel_requested(job_id)

    async def _heartbeat_loop(self, job_id: str) -> None:
        while True:
            await asyncio.sleep(HEARTBEAT_INTERVAL_S)
            try:
                async with self._db.session() as session:
                    await JobRepository(session).heartbeat(job_id)
            except Exception:  # a missed heartbeat must never kill the job
                logger.warning("heartbeat failed", exc_info=True)

    # --- pipeline --------------------------------------------------------------------------------

    async def run(self, job_id: str) -> JobStatus:
        token = job_id_var.set(job_id)
        heartbeat = asyncio.create_task(self._heartbeat_loop(job_id), name=f"heartbeat-{job_id}")
        JOBS_RUNNING.inc()
        provider_name = "unknown"
        try:
            async with self._db.session() as session:
                job = await JobRepository(session).get(job_id)
            if job is None:
                logger.warning("claimed job vanished")
                return JobStatus.FAILED
            provider_name = job.provider
            llm = self._registry.get(job.provider)
            state = PipelineState(
                spec=job.spec, limits=self._limits, max_repair_rounds=self._max_repair_rounds
            )
            await self._emit(job_id, "job.started", provider=llm.name, model=llm.model)
            status = await self._run_stages(job_id, state, llm)
        except asyncio.CancelledError:
            await asyncio.shield(self._handle_interrupt(job_id))
            raise
        except Exception as exc:  # defensive: anything unexpected fails the job, not the worker
            logger.exception("pipeline crashed")
            status = await self._fail(job_id, None, f"Internal error: {type(exc).__name__}")
        finally:
            heartbeat.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await heartbeat
            JOBS_RUNNING.dec()
            job_id_var.reset(token)
        JOBS_FINISHED.labels(provider_name, status.value).inc()
        return status

    async def _run_stages(self, job_id: str, state: PipelineState, llm: LLMProvider) -> JobStatus:
        for agent in self._agents:
            stage = agent.stage
            if await self._cancel_requested(job_id):
                return await self._cancelled(job_id)

            await self._update_job(job_id, current_stage=stage.value)
            await self._update_stage(job_id, stage, status=StageStatus.RUNNING, started_at=utcnow())
            await self._emit(job_id, "stage.started", stage=stage.value, label=STAGE_LABELS[stage])

            async def log(message: str, _stage: StageName = stage) -> None:
                await self._emit(job_id, "stage.log", stage=_stage.value, message=message)

            started = time.perf_counter()
            try:
                result = await agent.run(state, llm, log)
            except LLMError as exc:
                self._observe(stage, llm.name, "error", started)
                return await self._fail(job_id, stage, f"{STAGE_LABELS[stage]}: {exc}")
            except Exception as exc:
                logger.exception("stage crashed", extra={"stage": stage.value})
                self._observe(stage, llm.name, "error", started)
                return await self._fail(
                    job_id, stage, f"{STAGE_LABELS[stage]} crashed ({type(exc).__name__})"
                )
            duration = self._observe(stage, llm.name, "ok", started)
            await self._persist_stage(job_id, stage, state, result, duration, llm.name)

        return await self._succeed(job_id, state)

    def _observe(self, stage: StageName, provider: str, outcome: str, started: float) -> float:
        duration = time.perf_counter() - started
        STAGE_DURATION.labels(stage.value, provider, outcome).observe(duration)
        return duration

    async def _persist_stage(
        self,
        job_id: str,
        stage: StageName,
        state: PipelineState,
        result: StageResult,
        duration: float,
        provider: str,
    ) -> None:
        async with self._db.session() as session:
            repo = JobRepository(session)
            await repo.upsert_files(job_id, (state.files[p] for p in result.files), stage.value)
            job = await repo.get(job_id)
            assert job is not None
            artifacts: dict[str, Any] = {
                "input_tokens": job.input_tokens + result.usage.input_tokens,
                "output_tokens": job.output_tokens + result.usage.output_tokens,
            }
            if stage is StageName.ANALYZE and state.requirements:
                artifacts["requirements"] = state.requirements.model_dump()
                artifacts["project_name"] = state.requirements.project_name[:64]
            if stage is StageName.ARCHITECT and state.architecture:
                artifacts["architecture"] = state.architecture.model_dump()
            if stage is StageName.REVIEW:
                if state.validation:
                    artifacts["validation"] = state.validation.model_dump()
                if state.review:
                    artifacts["review"] = state.review.model_dump()
            await repo.update_job(job_id, **artifacts)
            await repo.update_stage(
                job_id,
                stage.value,
                status=StageStatus.SUCCEEDED,
                finished_at=utcnow(),
                summary=result.summary,
                input_tokens=result.usage.input_tokens,
                output_tokens=result.usage.output_tokens,
            )
        LLM_TOKENS.labels(provider, "input").inc(result.usage.input_tokens)
        LLM_TOKENS.labels(provider, "output").inc(result.usage.output_tokens)
        await self._emit(
            job_id,
            "stage.completed",
            stage=stage.value,
            summary=result.summary,
            duration_s=round(duration, 3),
            input_tokens=result.usage.input_tokens,
            output_tokens=result.usage.output_tokens,
            files=list(result.files),
        )

    async def _succeed(self, job_id: str, state: PipelineState) -> JobStatus:
        report = state.validation
        if report is not None:
            for issue in report.issues:
                VALIDATION_ISSUES.labels(issue.severity).inc()
        await self._update_job(
            job_id, status=JobStatus.SUCCEEDED, current_stage=None, finished_at=utcnow()
        )
        await self._emit(
            job_id,
            "job.succeeded",
            files=len(state.files),
            validation_passed=report.passed if report else None,
            score=state.review.score if state.review else None,
        )
        logger.info("job succeeded", extra={"files": len(state.files)})
        return JobStatus.SUCCEEDED

    async def _fail(self, job_id: str, stage: StageName | None, message: str) -> JobStatus:
        if stage is not None:
            await self._update_stage(
                job_id, stage, status=StageStatus.FAILED, finished_at=utcnow(), summary=message
            )
            await self._emit(job_id, "stage.failed", stage=stage.value, error=message)
        async with self._db.session() as session:
            await JobRepository(session).skip_pending_stages(job_id)
        await self._update_job(
            job_id, status=JobStatus.FAILED, error=message, current_stage=None, finished_at=utcnow()
        )
        await self._emit(job_id, "job.failed", error=message)
        logger.warning("job failed", extra={"error": message})
        return JobStatus.FAILED

    async def _cancelled(self, job_id: str) -> JobStatus:
        async with self._db.session() as session:
            await JobRepository(session).skip_pending_stages(job_id)
        await self._update_job(
            job_id, status=JobStatus.CANCELLED, current_stage=None, finished_at=utcnow()
        )
        await self._emit(job_id, "job.cancelled")
        return JobStatus.CANCELLED

    async def _handle_interrupt(self, job_id: str) -> None:
        """The task was cancelled: honour a user cancel, otherwise requeue (graceful shutdown)."""
        try:
            if await self._cancel_requested(job_id):
                await self._cancelled(job_id)
                JOBS_FINISHED.labels("unknown", JobStatus.CANCELLED.value).inc()
            else:
                async with self._db.session() as session:
                    await JobRepository(session).reset_for_retry(job_id)
                self._notifier.notify_job(job_id)
        except Exception:
            logger.exception("could not record interruption")
