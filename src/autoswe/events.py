"""In-process wake-ups for SSE streams and the worker loop.

The database is the source of truth (events are persisted, the queue is the ``jobs`` table), so
these signals only reduce latency. Consumers always fall back to polling on a short timeout,
which keeps multi-replica deployments correct even though a signal never crosses processes.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections import defaultdict


class JobNotifier:
    def __init__(self) -> None:
        self._job_waiters: defaultdict[str, set[asyncio.Event]] = defaultdict(set)
        self._queue_event = asyncio.Event()

    def notify_job(self, job_id: str) -> None:
        for waiter in self._job_waiters.get(job_id, ()):
            waiter.set()

    async def wait_for_job(self, job_id: str, max_wait_s: float) -> None:
        waiter = asyncio.Event()
        self._job_waiters[job_id].add(waiter)
        try:
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(waiter.wait(), max_wait_s)
        finally:
            waiters = self._job_waiters.get(job_id)
            if waiters is not None:
                waiters.discard(waiter)
                if not waiters:
                    del self._job_waiters[job_id]

    def notify_queue(self) -> None:
        self._queue_event.set()

    async def wait_for_queue(self, max_wait_s: float) -> None:
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(self._queue_event.wait(), max_wait_s)
        self._queue_event.clear()
