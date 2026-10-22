"""Operational endpoints: liveness, readiness, metrics and server info."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Response, status
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from sqlalchemy import text

from autoswe import __version__
from autoswe.api.deps import ServicesDep
from autoswe.observability import REGISTRY
from autoswe.schemas import ProviderOut, ServerInfo

router = APIRouter(tags=["system"])


@router.get("/healthz")
async def healthz() -> dict[str, str]:
    """Liveness: the process is up and serving requests."""
    return {"status": "ok"}


@router.get("/readyz")
async def readyz(services: ServicesDep, response: Response) -> dict[str, Any]:
    """Readiness: the database answers and (if enabled) the worker loop is alive."""
    checks: dict[str, str] = {}
    try:
        async with services.db.session() as session:
            await session.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception as exc:
        checks["database"] = f"error: {type(exc).__name__}"
    if services.settings.worker_enabled:
        checks["worker"] = "ok" if services.worker is not None else "error: not running"
    ready = all(value == "ok" for value in checks.values())
    if not ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return {"status": "ok" if ready else "unavailable", "checks": checks}


@router.get("/metrics", include_in_schema=False)
async def metrics(services: ServicesDep) -> Response:
    if not services.settings.metrics_enabled:
        return Response(status_code=status.HTTP_404_NOT_FOUND)
    return Response(generate_latest(REGISTRY), media_type=CONTENT_TYPE_LATEST)


@router.get("/api/v1/info", response_model=ServerInfo)
async def server_info(services: ServicesDep) -> ServerInfo:
    """Unauthenticated discovery endpoint the UI uses to configure itself."""
    return ServerInfo(
        version=__version__,
        auth_required=not services.auth.disabled,
        providers=[
            ProviderOut(name=p.name, model=p.model, default=p.default)
            for p in services.registry.describe()
        ],
    )
