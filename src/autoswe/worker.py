"""Background job runner.

The queue is the ``jobs`` table: the worker claims queued rows with a compare-and-set update and
runs up to ``concurrency`` pipelines at once as asyncio tasks. Because claiming is atomic and
progress is heart-beaten, any number of API replicas can run a worker side by side; a reaper
fails jobs whose worker disappeared.
"""

from __future__ import annotations

import asyncio
import contextlib
import functools
import logging
import os
import socket
import time
import uuid
from datetime import timedelta

from autoswe.db import Database
from autoswe.events import JobNotifier
from autoswe.observability import JOBS_QUEUED
from autoswe.pipeline.orchestrator import Orchestrator
from autoswe.repository import JobRepository

logger = logging.getLogger(__name__)

REAP_INTERVAL_S = 30.0


class Worker:
    def __init__(
        self,
        db: Database,
        orchestrator: Orchestrator,
        notifier: JobNotifier,
        *,
        concurrency: int,
        poll_interval_s: float,
        stale_after_s: float,
        drain_s: float = 0.0,
    ) -> None:
        self.id = f"{socket.gethostname()}-{os.getpid()}-{uuid.uuid4().hex[:6]}"
        self._db = db
        self._orchestrator = orchestrator
        self._notifier = notifier
        self._concurrency = concurrency
        self._poll_interval_s = poll_interval_s
        self._stale_after = timedelta(seconds=stale_after_s)
        self._drain_s = drain_s
        self._tasks: dict[str, asyncio.Task[object]] = {}
        self._loop_task: asyncio.Task[None] | None = None
        self._stopping = False
        self._last_reap = 0.0

    @property
    def running_jobs(self) -> set[str]:
        return set(self._tasks)

    async def start(self) -> None:
        await self.reap()
        self._loop_task = asyncio.create_task(self._loop(), name="autoswe-worker")
        logger.info(
            "worker started", extra={"worker_id": self.id, "concurrency": self._concurrency}
        )

    async def reap(self) -> list[str]:
        self._last_reap = time.monotonic()
        async with self._db.session() as session:
            stale = await JobRepository(session).fail_stale(self._stale_after)
        for job_id in stale:
            logger.warning("reaped stale job", extra={"job_id": job_id})
            self._notifier.notify_job(job_id)
        return stale

    async def fill_slots(self) -> list[str]:
        """Claim queued jobs until all slots are busy; returns the claimed job IDs."""
        claimed: list[str] = []
        while len(self._tasks) < self._concurrency and not self._stopping:
            async with self._db.session() as session:
                job_id = await JobRepository(session).claim_next(self.id)
            if job_id is None:
                break
            claimed.append(job_id)
            task = asyncio.create_task(self._orchestrator.run(job_id), name=f"job-{job_id}")
            self._tasks[job_id] = task
            task.add_done_callback(functools.partial(self._on_done, job_id))
        async with self._db.session() as session:
            JOBS_QUEUED.set(await JobRepository(session).count_queued())
        return claimed

    def _on_done(self, job_id: str, _task: asyncio.Task[object]) -> None:
        self._tasks.pop(job_id, None)
        self._notifier.notify_queue()

    async def _loop(self) -> None:
        while not self._stopping:
            try:
                if time.monotonic() - self._last_reap > REAP_INTERVAL_S:
                    await self.reap()
                await self.fill_slots()
            except Exception:
                logger.exception("worker loop iteration failed")
            await self._notifier.wait_for_queue(self._poll_interval_s)

    def cancel_local(self, job_id: str) -> bool:
        task = self._tasks.get(job_id)
        if task is None or task.done():
            return False
        task.cancel()
        return True

    async def wait_idle(self) -> None:
        """Wait until every in-flight job task has finished (used by tests)."""
        while self._tasks:
            await asyncio.gather(*self._tasks.values(), return_exceptions=True)

    async def stop(self) -> None:
        """Graceful shutdown: stop claiming, drain, then hand back what is still running.

        Jobs that finish within ``drain_s`` complete normally; the rest are cancelled, and the
        orchestrator's cancellation handler requeues them for another worker. Either way no job
        is lost; a hard kill (no SIGTERM handling) is covered by the stale-job reaper.
        """
        self._stopping = True
        if self._loop_task is not None:
            self._loop_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._loop_task
        if self._tasks and self._drain_s > 0:
            logger.info("draining in-flight jobs",
                        extra={"jobs": len(self._tasks), "drain_s": self._drain_s})  # fmt: skip
            await asyncio.wait(list(self._tasks.values()), timeout=self._drain_s)
        remaining = [task for task in self._tasks.values() if not task.done()]
        for task in remaining:
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks.values(), return_exceptions=True)
        logger.info("worker stopped", extra={"worker_id": self.id, "requeued": len(remaining)})
