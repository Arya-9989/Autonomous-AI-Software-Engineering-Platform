from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from autoswe.app import create_app
from autoswe.config import Settings
from autoswe.llm.registry import ProviderRegistry
from autoswe.worker import Worker

ALICE_KEY = "alice-test-key-0123456789"
BOB_KEY = "bob-test-key-9876543210ab"
ALICE = {"Authorization": f"Bearer {ALICE_KEY}"}
BOB = {"X-API-Key": BOB_KEY}

LIBRARY_SPEC = (
    "Build a REST API for a small library where members borrow books and librarians "
    "track loans with due dates."
)


def make_settings(tmp_path: Path, **overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "_env_file": None,
        "environment": "test",
        "database_url": f"sqlite+aiosqlite:///{(tmp_path / 'test.db').as_posix()}",
        "api_keys": f"alice:{ALICE_KEY},bob:{BOB_KEY}",
        "log_format": "console",
        "log_level": "WARNING",
        "worker_poll_interval_s": 0.05,
        "worker_drain_s": 0,  # tests that drain say so
        "static_dir": tmp_path / "no-frontend",
    }
    values.update(overrides)
    return Settings(**values)


class AppHarness:
    def __init__(self, app: FastAPI, client: httpx.AsyncClient) -> None:
        self.app = app
        self.client = client

    @property
    def worker(self) -> Worker:
        worker = self.app.state.services.worker
        assert worker is not None
        return worker  # type: ignore[no-any-return]

    async def submit(self, spec: str = LIBRARY_SPEC, headers: dict[str, str] = ALICE,
                     **extra: Any) -> dict[str, Any]:  # fmt: skip
        response = await self.client.post("/api/v1/jobs", json={"spec": spec, **extra},
                                          headers=headers)  # fmt: skip
        assert response.status_code == 202, response.text
        return response.json()  # type: ignore[no-any-return]

    async def wait_for(
        self,
        job_id: str,
        statuses: set[str],
        headers: dict[str, str] = ALICE,
        max_wait_s: float = 20,
    ) -> dict[str, Any]:
        deadline = asyncio.get_running_loop().time() + max_wait_s
        while True:
            body = (await self.client.get(f"/api/v1/jobs/{job_id}", headers=headers)).json()
            if body["status"] in statuses:
                return body  # type: ignore[no-any-return]
            if asyncio.get_running_loop().time() > deadline:
                raise AssertionError(f"job stuck in {body['status']}")
            await asyncio.sleep(0.05)


HarnessFactory = Callable[..., Any]


@pytest.fixture
async def harness_factory(tmp_path: Path) -> AsyncIterator[HarnessFactory]:
    stack: list[tuple[FastAPI, httpx.AsyncClient, Any]] = []

    async def factory(registry: ProviderRegistry | None = None, **overrides: Any) -> AppHarness:
        settings = make_settings(tmp_path, **overrides)
        app = create_app(settings, registry=registry)
        lifespan = app.router.lifespan_context(app)
        await lifespan.__aenter__()
        client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")
        stack.append((app, client, lifespan))
        return AppHarness(app, client)

    yield factory
    for _app, client, lifespan in reversed(stack):
        await client.aclose()
        await lifespan.__aexit__(None, None, None)


@pytest.fixture
async def harness(harness_factory: HarnessFactory) -> AppHarness:
    return await harness_factory()  # type: ignore[no-any-return]
