"""The sandbox runner's job API and the API-side client that calls it.

The runner is the only process with Docker access, so its request handling is the security
boundary between the API and the container engine: these tests try to get past it.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Sequence
from typing import Any

import httpx2
import pytest

from autoswe.pipeline.artifacts import GeneratedFile
from autoswe.sandbox.backends import LocalProcessSandbox, RemoteSandbox
from autoswe.sandbox.models import SandboxLimits, SandboxResult, SandboxStatus
from autoswe.sandbox.service import RunnerSettings, build_runner_sandbox, create_runner_app
from tests import sandbox_projects as projects

TOKEN = "runner-test-token-0123456789"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


class RecordingSandbox:
    name = "fake"

    def __init__(self, gate: asyncio.Event | None = None) -> None:
        self.runs: list[list[str]] = []
        self.gate = gate

    async def run(self, files: Sequence[GeneratedFile]) -> SandboxResult:
        self.runs.append([f.path for f in files])
        if self.gate is not None:
            await self.gate.wait()
        return SandboxResult(status=SandboxStatus.PASSED, backend="fake", isolation="test")


def _settings(**overrides: Any) -> RunnerSettings:
    values: dict[str, Any] = {
        "_env_file": None,
        "sandbox_runner_token": TOKEN,
        "log_format": "console",
        "log_level": "WARNING",
    }
    return RunnerSettings(**{**values, **overrides})


def _client(app: Any) -> httpx2.AsyncClient:
    return httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app), base_url="http://runner")


def _body(files: list[GeneratedFile] | None = None) -> dict[str, Any]:
    return {"files": [f.model_dump() for f in (files or projects.passing())]}


async def test_authenticated_run_returns_the_result() -> None:
    fake = RecordingSandbox()
    async with _client(create_runner_app(_settings(), sandbox=fake)) as client:
        response = await client.post("/v1/runs", json=_body(), headers=AUTH)
    assert response.status_code == 200
    assert SandboxResult.model_validate(response.json()).status is SandboxStatus.PASSED
    assert "app/calc.py" in fake.runs[0]


@pytest.mark.parametrize(
    "headers",
    [{}, {"Authorization": "Bearer wrong-token-wrong-token"}, {"Authorization": TOKEN},
     {"X-API-Key": TOKEN}],
)  # fmt: skip
async def test_runs_need_the_runner_token(headers: dict[str, str]) -> None:
    fake = RecordingSandbox()
    async with _client(create_runner_app(_settings(), sandbox=fake)) as client:
        response = await client.post("/v1/runs", json=_body(), headers=headers)
    assert response.status_code == 401
    assert fake.runs == []


async def test_callers_cannot_choose_image_command_or_limits() -> None:
    fake = RecordingSandbox()
    async with _client(create_runner_app(_settings(), sandbox=fake)) as client:
        for extra in ({"image": "alpine"}, {"command": ["sh", "-c", "id"]},
                      {"limits": {"memory_mb": 99999}}, {"privileged": True}):  # fmt: skip
            response = await client.post("/v1/runs", json={**_body(), **extra}, headers=AUTH)
            assert response.status_code == 422, extra
    assert fake.runs == []


@pytest.mark.parametrize("path", ["../escape.py", "/etc/passwd", "app/../../x.py", ".git/config"])
async def test_unsafe_paths_are_rejected_before_anything_runs(path: str) -> None:
    fake = RecordingSandbox()
    files = [*projects.passing(), GeneratedFile(path=path, content="x")]
    async with _client(create_runner_app(_settings(), sandbox=fake)) as client:
        response = await client.post("/v1/runs", json=_body(files), headers=AUTH)
    assert response.status_code == 422
    assert "path-safety" in response.text
    assert fake.runs == []


async def test_oversized_requests_are_refused_while_streaming() -> None:
    fake = RecordingSandbox()
    app = create_runner_app(_settings(sandbox_runner_max_request_bytes=5_000), sandbox=fake)
    big = [GeneratedFile(path="big.py", content="x" * 10_000)]
    async with _client(app) as client:
        response = await client.post("/v1/runs", json=_body(big), headers=AUTH)
        assert response.status_code == 413

        async def chunks() -> Any:  # no Content-Length: the cap applies to the stream itself
            for _ in range(10):
                yield b"x" * 1_000

        streamed = await client.post("/v1/runs", content=chunks(), headers=AUTH)
        assert streamed.status_code == 413
    assert fake.runs == []


async def test_malformed_json_is_a_422() -> None:
    async with _client(create_runner_app(_settings(), sandbox=RecordingSandbox())) as client:
        response = await client.post("/v1/runs", content=b"{not json", headers=AUTH)
    assert response.status_code == 422


async def test_the_runner_refuses_work_beyond_its_queue() -> None:
    gate = asyncio.Event()
    fake = RecordingSandbox(gate)
    app = create_runner_app(
        _settings(sandbox_max_concurrent_runs=1, sandbox_runner_max_queue=1), sandbox=fake
    )
    async with _client(app) as client:
        first = asyncio.create_task(client.post("/v1/runs", json=_body(), headers=AUTH))
        second = asyncio.create_task(client.post("/v1/runs", json=_body(), headers=AUTH))
        while len(fake.runs) < 2:  # noqa: ASYNC110 - polling a test fake for both runs
            await asyncio.sleep(0.01)
        third = await client.post("/v1/runs", json=_body(), headers=AUTH)
        assert third.status_code == 503
        assert third.headers["retry-after"] == "5"
        gate.set()
        assert (await first).status_code == (await second).status_code == 200
        # Capacity is released once runs finish.
        assert (await client.post("/v1/runs", json=_body(), headers=AUTH)).status_code == 200


async def test_probes_and_no_other_routes() -> None:
    async with _client(create_runner_app(_settings(), sandbox=RecordingSandbox())) as client:
        assert (await client.get("/healthz")).json() == {"status": "ok"}
        assert (await client.get("/readyz")).json() == {"status": "ok", "backend": "fake"}
        for path in ("/docs", "/openapi.json", "/v1/runs/1", "/api/v1/jobs"):
            assert (await client.get(path)).status_code in {404, 405}


def test_runner_settings_require_a_token() -> None:
    with pytest.raises(ValueError):  # noqa: PT011
        RunnerSettings(_env_file=None)
    with pytest.raises(ValueError):  # noqa: PT011
        _settings(sandbox_runner_token="short")


def test_runner_builds_its_backend_from_settings() -> None:
    assert build_runner_sandbox(_settings()).name == "docker"
    assert isinstance(build_runner_sandbox(_settings(sandbox_runner_backend="local")),
                      LocalProcessSandbox)  # fmt: skip


async def test_remote_client_round_trip_through_a_real_runner() -> None:
    """RemoteSandbox -> runner app -> local backend -> real pytest, over the HTTP API."""
    runner = create_runner_app(
        _settings(), sandbox=LocalProcessSandbox(SandboxLimits(timeout_s=60, typecheck=False))
    )
    remote = RemoteSandbox("http://runner/", TOKEN, timeout_s=60,
                           transport=httpx2.ASGITransport(app=runner))  # fmt: skip
    result = await remote.run(projects.failing())
    assert result.status is SandboxStatus.FAILED
    assert result.backend == "local"
    assert result.tests is not None and result.tests.failed == 2


def _remote(handler: Any, retries: int = 2) -> RemoteSandbox:
    return RemoteSandbox("http://runner", TOKEN, timeout_s=5, retries=retries,
                         transport=httpx2.MockTransport(handler))  # fmt: skip


async def test_remote_client_maps_failures_to_sandbox_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def no_sleep(_: float) -> None:
        return None

    monkeypatch.setattr(asyncio, "sleep", no_sleep)
    seen: list[httpx2.Request] = []

    def refuse(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        raise httpx2.ConnectError("refused", request=request)

    result = await _remote(refuse).run(projects.passing())
    assert result.status is SandboxStatus.ERROR and "cannot reach" in result.message
    assert len(seen) == 3  # retried: a run is a pure function of its files

    busy = await _remote(lambda r: httpx2.Response(503)).run(projects.passing())
    assert "HTTP 503" in busy.message
    denied = await _remote(lambda r: httpx2.Response(401)).run(projects.passing())
    assert "HTTP 401" in denied.message
    garbage = await _remote(lambda r: httpx2.Response(200, text="nope")).run(projects.passing())
    assert "unreadable" in garbage.message

    def slow(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ReadTimeout("slow", request=request)

    assert "in time" in (await _remote(slow).run(projects.passing())).message


async def test_remote_client_sends_the_token_and_files() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx2.Request) -> httpx2.Response:
        captured["auth"] = request.headers["authorization"]
        captured["body"] = json.loads(request.content)
        ok = SandboxResult(status=SandboxStatus.PASSED, backend="docker", isolation="i")
        return httpx2.Response(200, json=ok.model_dump(mode="json"))

    result = await _remote(handler).run(projects.passing())
    assert result.status is SandboxStatus.PASSED
    assert captured["auth"] == f"Bearer {TOKEN}"
    assert {f["path"] for f in captured["body"]["files"]} >= {"app/calc.py", "tests/test_calc.py"}
