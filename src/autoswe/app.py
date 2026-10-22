"""Application factory and lifespan wiring."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from autoswe import __version__
from autoswe.api import jobs, system
from autoswe.api.deps import Services
from autoswe.config import Settings, get_settings
from autoswe.db import Database
from autoswe.events import JobNotifier
from autoswe.llm.registry import ProviderRegistry, build_registry
from autoswe.logging_config import configure_logging
from autoswe.migrations.runner import upgrade_to_head
from autoswe.observability import ObservabilityMiddleware
from autoswe.pipeline.orchestrator import Orchestrator
from autoswe.pipeline.validation import Limits
from autoswe.security import ApiKeyAuthenticator
from autoswe.worker import Worker

logger = logging.getLogger(__name__)


def build_services(
    settings: Settings, registry: ProviderRegistry | None = None
) -> tuple[Services, Orchestrator]:
    db = Database(settings.database_url, echo=settings.db_echo)
    registry = registry or build_registry(settings)
    notifier = JobNotifier()
    orchestrator = Orchestrator(
        db,
        registry,
        notifier,
        limits=Limits(
            max_files=settings.max_generated_files,
            max_file_bytes=settings.max_file_bytes,
            max_total_bytes=settings.max_total_bytes,
        ),
        max_repair_rounds=settings.max_repair_rounds,
    )
    services = Services(
        settings=settings,
        db=db,
        registry=registry,
        notifier=notifier,
        auth=ApiKeyAuthenticator.from_settings(settings),
        worker=None,
    )
    return services, orchestrator


def _mount_frontend(app: FastAPI, static_dir: Path) -> None:
    index = static_dir / "index.html"
    assets = static_dir / "assets"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=assets), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    def spa(full_path: str) -> FileResponse:  # sync: filesystem checks run in the threadpool
        if full_path.startswith(("api/", "assets/")):
            raise HTTPException(status.HTTP_404_NOT_FOUND)
        candidate = (static_dir / full_path).resolve()
        if full_path and candidate.is_file() and candidate.is_relative_to(static_dir.resolve()):
            return FileResponse(candidate)
        return FileResponse(index, headers={"Cache-Control": "no-cache"})


def create_app(
    settings: Settings | None = None, *, registry: ProviderRegistry | None = None
) -> FastAPI:
    """Build the ASGI app. ``registry`` lets tests inject scripted LLM providers."""
    settings = settings or get_settings()
    configure_logging(settings.log_level, settings.log_format)
    services, orchestrator = build_services(settings, registry)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if settings.db_auto_migrate:
            await asyncio.to_thread(upgrade_to_head, settings.database_url)
        worker: Worker | None = None
        if settings.worker_enabled:
            worker = Worker(
                services.db,
                orchestrator,
                services.notifier,
                concurrency=settings.worker_concurrency,
                poll_interval_s=settings.worker_poll_interval_s,
                stale_after_s=settings.job_stale_after_s,
            )
            await worker.start()
        services.worker = worker
        logger.info(
            "autoswe started",
            extra={
                "version": __version__,
                "providers": [p.name for p in services.registry.describe()],
                "default_provider": services.registry.default,
                "auth": "disabled" if services.auth.disabled else "api-key",
            },
        )
        try:
            yield
        finally:
            if worker is not None:
                await worker.stop()
            await services.db.dispose()

    app = FastAPI(
        title="Autonomous AI Software Engineering Platform",
        version=__version__,
        description=(
            "Submit a natural-language spec; a pipeline of agents (requirements analyst, "
            "architect, code generator, DevOps engineer, reviewer) produces a validated project "
            "with Docker, CI, Kubernetes and Terraform."
        ),
        docs_url="/api/docs",
        redoc_url=None,
        openapi_url="/api/openapi.json",
        debug=settings.debug,
        lifespan=lifespan,
    )
    app.state.services = services
    app.state.orchestrator = orchestrator

    app.add_middleware(ObservabilityMiddleware)
    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origins,
            allow_methods=["GET", "POST", "DELETE"],
            allow_headers=["Authorization", "X-API-Key", "Content-Type", "Last-Event-ID"],
        )

    @app.exception_handler(RequestValidationError)
    async def validation_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
        errors = [
            {"loc": list(e.get("loc", ())), "msg": e.get("msg", ""), "type": e.get("type", "")}
            for e in exc.errors()
        ]
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            content={"detail": "Request validation failed", "errors": errors},
        )

    @app.exception_handler(Exception)
    async def unhandled_handler(_: Request, exc: Exception) -> JSONResponse:
        logger.exception("unhandled error", exc_info=exc)
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"detail": "Internal server error"},
        )

    app.include_router(system.router)
    app.include_router(jobs.router)

    static_dir = settings.resolved_static_dir
    if static_dir is not None:
        _mount_frontend(app, static_dir)
    else:

        @app.get("/", include_in_schema=False)
        async def root() -> dict[str, str]:
            return {"service": "autoswe", "docs": "/api/docs", "ui": "frontend not built"}

    return app
