"""The sandbox runner: a separate service that is the only holder of Docker access.

Its whole API is ``POST /v1/runs`` (files in, :class:`SandboxResult` out) plus health probes.
The caller chooses nothing but file contents: image, command, limits, runtime and isolation
flags come from the runner's own configuration. Requests are authenticated with a shared
token (compared in constant time), bodies are size-capped while they stream in, paths go
through the same safety rules as the pipeline, and the number of queued runs is bounded.
See ``docs/adr/0001-sandboxed-test-execution.md`` for why this is a separate process.
"""

from __future__ import annotations

import hmac
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated, Literal

from fastapi import FastAPI, Header, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError

from autoswe.config import SandboxSettings
from autoswe.logging_config import configure_logging
from autoswe.observability import ObservabilityMiddleware, untraced
from autoswe.pipeline.artifacts import GeneratedFile
from autoswe.pipeline.validation import Limits, sanitize_files
from autoswe.sandbox.backends import DockerSandbox, LocalProcessSandbox, Sandbox
from autoswe.sandbox.models import SandboxResult
from autoswe.stacks import Stack
from autoswe.tracing import configure_tracing

logger = logging.getLogger(__name__)


class RunnerSettings(SandboxSettings):
    sandbox_runner_token: SecretStr  # required here: the runner never runs unauthenticated
    # docker in every deployment; local exists so the API contract can be tested without Docker.
    sandbox_runner_backend: Literal["docker", "local"] = "docker"
    sandbox_runner_host: str = "0.0.0.0"  # noqa: S104 - container default, internal network
    sandbox_runner_port: int = 8080
    sandbox_runner_max_queue: int = Field(default=8, ge=0, le=256)
    sandbox_runner_max_request_bytes: int = Field(default=10_000_000, ge=1_000)
    log_level: str = "INFO"
    log_format: Literal["json", "console"] = "json"


class RunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    files: list[GeneratedFile] = Field(max_length=500)
    # A profile name, not an image: the runner maps it to its own configured image.
    stack: Stack = Stack.PYTHON


class _Gate:
    """At most ``slots`` runs at once and ``queue`` more waiting; beyond that, refuse (503)."""

    def __init__(self, slots: int, queue: int) -> None:
        self._capacity = slots + queue
        self._admitted = 0

    def try_enter(self) -> bool:
        if self._admitted >= self._capacity:
            return False
        self._admitted += 1
        return True

    def leave(self) -> None:
        self._admitted -= 1


def build_runner_sandbox(settings: RunnerSettings) -> Sandbox:
    limits = settings.sandbox_limits()
    if settings.sandbox_runner_backend == "local":
        logger.warning("sandbox runner uses the LOCAL backend: generated code is NOT isolated")
        return LocalProcessSandbox(limits)
    return DockerSandbox(
        settings.sandbox_image,
        limits,
        images=settings.sandbox_stack_images(),
        runtime=settings.sandbox_docker_runtime,
        docker=settings.sandbox_docker_binary,
        pool=settings.sandbox_pool,
        max_concurrent=settings.sandbox_max_concurrent_runs,
    )


def create_runner_app(  # noqa: PLR0915 - the app factory defines its handlers inline
    settings: RunnerSettings | None = None, *, sandbox: Sandbox | None = None
) -> FastAPI:
    settings = settings or RunnerSettings()  # the token comes from the env
    configure_logging(settings.log_level, settings.log_format)
    configure_tracing("autoswe-sandbox-runner")  # joins the job's trace via traceparent
    backend = sandbox or build_runner_sandbox(settings)
    token = settings.sandbox_runner_token.get_secret_value().encode()
    gate = _Gate(settings.sandbox_max_concurrent_runs, settings.sandbox_runner_max_queue)
    max_bytes = settings.sandbox_runner_max_request_bytes
    limits = Limits(max_files=500, max_file_bytes=1_000_000, max_total_bytes=max_bytes)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        if isinstance(backend, DockerSandbox):
            removed = await backend.remove_leftovers()
            if removed:
                logger.warning("removed leftover sandbox containers", extra={"count": removed})
        logger.info("sandbox runner started", extra={"backend": backend.name})
        yield

    app = FastAPI(
        title="AutoSWE sandbox runner", docs_url=None, redoc_url=None, openapi_url=None,
        lifespan=lifespan, telemetry={"operation_spans": False, "exclude": untraced},
    )  # fmt: skip
    app.add_middleware(ObservabilityMiddleware)

    def authenticate(authorization: str | None) -> None:
        scheme, _, presented = (authorization or "").partition(" ")
        if scheme.lower() != "bearer" or not hmac.compare_digest(presented.encode(), token):
            raise HTTPException(
                status.HTTP_401_UNAUTHORIZED,
                detail="Missing or invalid runner token",
                headers={"WWW-Authenticate": "Bearer"},
            )

    async def read_body(request: Request) -> bytes:
        declared = request.headers.get("content-length")
        if declared is not None and declared.isdigit() and int(declared) > max_bytes:
            raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, detail="Request too large")
        chunks: list[bytes] = []
        size = 0
        async for chunk in request.stream():  # enforced while streaming, not after
            size += len(chunk)
            if size > max_bytes:
                raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, detail="Request too large")
            chunks.append(chunk)
        return b"".join(chunks)

    @app.post("/v1/runs", response_model=SandboxResult)
    async def run(
        request: Request, authorization: Annotated[str | None, Header()] = None
    ) -> SandboxResult:
        authenticate(authorization)
        try:
            payload = RunRequest.model_validate_json(await read_body(request))
        except ValidationError as exc:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=f"Invalid run request ({exc.error_count()} error(s))",
            ) from None
        clean, rejected = sanitize_files(payload.files, limits)
        if rejected:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=[f"{issue.rule}: {issue.message}" for issue in rejected[:10]],
            )
        if not gate.try_enter():
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Sandbox runner is at capacity",
                headers={"Retry-After": "5"},
            )
        try:
            result = await backend.run(clean, payload.stack)
        finally:
            gate.leave()
        logger.info(
            "sandbox run finished",
            extra={"status": result.status.value, "duration_s": result.duration_s,
                   "files": len(clean)},
        )  # fmt: skip
        return result

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/readyz")
    async def readyz() -> dict[str, str]:
        if isinstance(backend, DockerSandbox):
            ok, detail = await backend.ready()
            if not ok:
                raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail=detail)
        return {"status": "ok", "backend": backend.name}

    return app


def serve(settings: RunnerSettings | None = None) -> None:
    import uvicorn  # noqa: PLC0415

    settings = settings or RunnerSettings()
    uvicorn.run(
        create_runner_app(settings),
        host=settings.sandbox_runner_host,
        port=settings.sandbox_runner_port,
        log_config=None,
        timeout_graceful_shutdown=int(settings.sandbox_timeout_s) + 30,
    )


__all__ = ["RunRequest", "RunnerSettings", "create_runner_app", "serve"]
