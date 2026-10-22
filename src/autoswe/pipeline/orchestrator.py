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

from opentelemetry import propagate
from opentelemetry.trace import Span, SpanKind

from autoswe.budget import BudgetGuard, BudgetPolicy, period_bounds
from autoswe.db import Database
from autoswe.events import JobNotifier
from autoswe.llm.base import LLMError, LLMProvider, TokenUsage, is_metered
from autoswe.llm.registry import ProviderRegistry
from autoswe.logging_config import job_id_var
from autoswe.metering import MeteredProvider, UsageMeter
from autoswe.models import Job, JobStatus, StageStatus, utcnow
from autoswe.observability import (
    JOBS_FINISHED,
    JOBS_RUNNING,
    STAGE_DURATION,
    VALIDATION_ISSUES,
)
from autoswe.pipeline.agents import Agent, PipelineState, StageResult, default_agents
from autoswe.pipeline.artifacts import STAGE_LABELS, StageName
from autoswe.pipeline.validation import Limits
from autoswe.pricing import PricingTable, usd
from autoswe.repository import JobRepository
from autoswe.sandbox.backends import Sandbox
from autoswe.tracing import mark_error, tracer

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
        sandbox: Sandbox | None = None,
        max_test_repair_rounds: int = 2,
        pricing: PricingTable | None = None,
        budget: BudgetPolicy | None = None,
    ) -> None:
        self._db = db
        self._registry = registry
        self._notifier = notifier
        self._limits = limits
        self._max_repair_rounds = max_repair_rounds
        self._agents = list(agents) if agents is not None else default_agents()
        self._sandbox = sandbox
        self._max_test_repair_rounds = max_test_repair_rounds
        self._pricing = pricing or PricingTable.load()
        self._budget = budget or BudgetPolicy(None, {}, "month", 200_000)

    # --- persistence helpers ----------------------------------------------------------------------

    async def _emit(self, job_id: str, type_: str, **payload: Any) -> None:
        async with self._db.session() as session:
            await JobRepository(session).add_event(job_id, type_, payload)
        self._notifier.notify_job(job_id)

    async def _update_job(self, job_id: str, **values: Any) -> None:
        async with self._db.session() as session:
            await JobRepository(session).update_job(job_id, **values)

    async def _finish(
        self, job_id: str, event_type: str, payload: dict[str, Any], **values: Any
    ) -> bool:
        async with self._db.session() as session:
            finished = await JobRepository(session).finish(job_id, event_type, payload, **values)
        if not finished:
            logger.warning("job left running state before it finished; outcome kept")
        self._notifier.notify_job(job_id)
        return finished

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

    async def _trace_parent(self, job_id: str) -> dict[str, Any]:
        try:
            async with self._db.session() as session:
                job = await JobRepository(session).get(job_id)
        except Exception:  # tracing must never decide whether a job runs
            return {}
        return dict(job.trace_context or {}) if job is not None else {}

    async def run(self, job_id: str) -> JobStatus:
        """Run one job inside an ``autoswe.job`` span that continues the submission's trace."""
        with tracer().start_as_current_span(
            "autoswe.job",
            context=propagate.extract(await self._trace_parent(job_id)),
            kind=SpanKind.CONSUMER,
            attributes={"autoswe.job.id": job_id},
            record_exception=False,
            set_status_on_exception=False,
        ) as span:
            try:
                status = await self._run(job_id, span)
            except asyncio.CancelledError:
                span.set_attribute("autoswe.job.interrupted", True)
                raise
            span.set_attribute("autoswe.job.status", status.value)
            if status is JobStatus.FAILED:
                mark_error(span, "job failed", "job_failed")
            return status

    async def _run(self, job_id: str, span: Span) -> JobStatus:
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
            llm = self._metered(job, self._registry.get(job.provider))
            span.set_attributes({
                "autoswe.job.owner": job.owner, "autoswe.provider": job.provider,
                "gen_ai.request.model": llm.model, "autoswe.request_id": job.request_id or "",
            })  # fmt: skip
            state = PipelineState(
                spec=job.spec,
                limits=self._limits,
                max_repair_rounds=self._max_repair_rounds,
                sandbox=self._sandbox,
                max_test_repair_rounds=self._max_test_repair_rounds,
            )
            await self._emit(job_id, "job.started", provider=llm.name, model=llm.model)
            status = await self._run_stages(job_id, state, llm)
            total = llm.meter.total
            span.set_attributes({
                "gen_ai.usage.input_tokens": total.input_tokens,
                "gen_ai.usage.output_tokens": total.output_tokens,
                "autoswe.cost_usd": usd(total.cost_microusd),
                "autoswe.llm.calls": llm.meter.calls,
            })  # fmt: skip
            if state.test_result is not None:
                span.set_attribute("autoswe.tests.status", state.test_result.status.value)
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

    def _metered(self, job: Job, provider: LLMProvider) -> MeteredProvider:
        """The job's provider, priced and budgeted. Totals continue from what the job already
        spent (a requeued job keeps its earlier attempt's usage)."""
        meter = UsageMeter(
            TokenUsage(
                input_tokens=job.input_tokens,
                output_tokens=job.output_tokens,
                cache_read_input_tokens=job.cache_read_input_tokens,
                cache_creation_input_tokens=job.cache_creation_input_tokens,
                cost_microusd=job.cost_microusd,
                unpriced_calls=job.unpriced_calls,
            )
        )
        guard = None
        if is_metered(provider):
            guard = BudgetGuard(
                self._db,
                self._budget,
                job_id=job.id,
                principal=job.owner,
                period_start=job.budget_period_start or period_bounds(self._budget.period)[0],
                reserved=job.tokens_reserved,
            )
        return MeteredProvider(provider, self._pricing, meter, guard)

    async def _persist_usage(self, job_id: str, llm: LLMProvider) -> None:
        if isinstance(llm, MeteredProvider):
            await self._update_job(job_id, **_usage_columns(llm.meter.total))

    async def _run_stages(self, job_id: str, state: PipelineState, llm: LLMProvider) -> JobStatus:
        meter = llm.meter if isinstance(llm, MeteredProvider) else UsageMeter()
        for agent in self._agents:
            stage = agent.stage
            if await self._cancel_requested(job_id):
                return await self._cancelled(job_id)

            await self._update_job(job_id, current_stage=stage.value)
            await self._update_stage(job_id, stage, status=StageStatus.RUNNING, started_at=utcnow())
            await self._emit(job_id, "stage.started", stage=stage.value, label=STAGE_LABELS[stage])

            async def log(message: str, _stage: StageName = stage) -> None:
                await self._emit(job_id, "stage.log", stage=_stage.value, message=message)

            with tracer().start_as_current_span(
                f"autoswe.stage {stage.value}",
                attributes={"autoswe.stage": stage.value, "autoswe.job.id": job_id},
                record_exception=False,
                set_status_on_exception=False,
            ) as span:
                failed = await self._run_stage(job_id, agent, state, llm, meter, log, span)
            if failed is not None:
                return failed

        return await self._succeed(job_id, state)

    async def _run_stage(
        self,
        job_id: str,
        agent: Agent,
        state: PipelineState,
        llm: LLMProvider,
        meter: UsageMeter,
        log: Any,
        span: Span,
    ) -> JobStatus | None:
        """One agent; returns the job's terminal status if the stage failed it."""
        stage = agent.stage
        started = time.perf_counter()
        before = meter.total
        try:
            result = await agent.run(state, llm, log)
        except LLMError as exc:
            self._observe(stage, llm.name, "error", started)
            await self._persist_usage(job_id, llm)
            code = getattr(exc, "code", None)
            mark_error(span, exc, code or type(exc).__name__)
            return await self._fail(job_id, stage, f"{STAGE_LABELS[stage]}: {exc}", code=code)
        except Exception as exc:
            logger.exception("stage crashed", extra={"stage": stage.value})
            self._observe(stage, llm.name, "error", started)
            await self._persist_usage(job_id, llm)
            mark_error(span, exc)
            return await self._fail(
                job_id, stage, f"{STAGE_LABELS[stage]} crashed ({type(exc).__name__})"
            )
        duration = self._observe(stage, llm.name, "ok", started)
        usage = meter.total - before
        span.set_attributes({
            "autoswe.stage.duration_s": round(duration, 3),
            "autoswe.stage.files": len(result.files),
            "gen_ai.usage.input_tokens": usage.input_tokens,
            "gen_ai.usage.output_tokens": usage.output_tokens,
            "gen_ai.usage.cache_read.input_tokens": usage.cache_read_input_tokens,
            "gen_ai.usage.cache_write.input_tokens": usage.cache_creation_input_tokens,
            "autoswe.cost_usd": usd(usage.cost_microusd),
        })  # fmt: skip
        await self._persist_stage(job_id, stage, state, result, duration, usage, meter.total)
        return None

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
        usage: TokenUsage,
        total: TokenUsage,
    ) -> None:
        async with self._db.session() as session:
            repo = JobRepository(session)
            await repo.upsert_files(job_id, (state.files[p] for p in result.files), stage.value)
            artifacts: dict[str, Any] = _usage_columns(total)
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
                artifacts["test_report"] = state.test_report()
            await repo.update_job(job_id, **artifacts)
            await repo.update_stage(
                job_id,
                stage.value,
                status=StageStatus.SUCCEEDED,
                finished_at=utcnow(),
                summary=result.summary,
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                cache_read_input_tokens=usage.cache_read_input_tokens,
                cache_creation_input_tokens=usage.cache_creation_input_tokens,
                cost_microusd=usage.cost_microusd,
            )
        await self._emit(
            job_id,
            "stage.completed",
            stage=stage.value,
            summary=result.summary,
            duration_s=round(duration, 3),
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cache_read_input_tokens=usage.cache_read_input_tokens,
            cache_creation_input_tokens=usage.cache_creation_input_tokens,
            cost_usd=usd(usage.cost_microusd),
            files=list(result.files),
        )

    async def _succeed(self, job_id: str, state: PipelineState) -> JobStatus:
        report = state.validation
        if report is not None:
            for issue in report.issues:
                VALIDATION_ISSUES.labels(issue.severity).inc()
        await self._finish(
            job_id,
            "job.succeeded",
            {
                "files": len(state.files),
                "validation_passed": report.passed if report else None,
                "tests": state.test_result.status.value if state.test_result else None,
                "score": state.review.score if state.review else None,
            },
            status=JobStatus.SUCCEEDED,
            current_stage=None,
            finished_at=utcnow(),
        )
        logger.info("job succeeded", extra={"files": len(state.files)})
        return JobStatus.SUCCEEDED

    async def _fail(
        self, job_id: str, stage: StageName | None, message: str, *, code: str | None = None
    ) -> JobStatus:
        if stage is not None:
            await self._update_stage(
                job_id, stage, status=StageStatus.FAILED, finished_at=utcnow(), summary=message
            )
            await self._emit(job_id, "stage.failed", stage=stage.value, error=message)
        async with self._db.session() as session:
            await JobRepository(session).skip_pending_stages(job_id)
        await self._finish(
            job_id,
            "job.failed",
            {"error": message, "code": code},
            status=JobStatus.FAILED,
            error=message,
            error_code=code,
            current_stage=None,
            finished_at=utcnow(),
        )
        logger.warning("job failed", extra={"error": message})
        return JobStatus.FAILED

    async def _cancelled(self, job_id: str) -> JobStatus:
        async with self._db.session() as session:
            await JobRepository(session).skip_pending_stages(job_id)
        await self._finish(
            job_id,
            "job.cancelled",
            {},
            status=JobStatus.CANCELLED,
            current_stage=None,
            finished_at=utcnow(),
        )
        return JobStatus.CANCELLED

    async def _handle_interrupt(self, job_id: str) -> None:
        """The task was cancelled: honour a user cancel, otherwise requeue (graceful shutdown)."""
        try:
            if await self._cancel_requested(job_id):
                await self._cancelled(job_id)
                JOBS_FINISHED.labels("unknown", JobStatus.CANCELLED.value).inc()
            else:
                async with self._db.session() as session:
                    requeued = await JobRepository(session).reset_for_retry(job_id)
                if requeued:
                    self._notifier.notify_queue()
                self._notifier.notify_job(job_id)
        except Exception:
            logger.exception("could not record interruption")


def _usage_columns(total: TokenUsage) -> dict[str, int]:
    return {
        "input_tokens": total.input_tokens,
        "output_tokens": total.output_tokens,
        "cache_read_input_tokens": total.cache_read_input_tokens,
        "cache_creation_input_tokens": total.cache_creation_input_tokens,
        "cost_microusd": total.cost_microusd,
        "unpriced_calls": total.unpriced_calls,
    }
