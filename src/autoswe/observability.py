"""Prometheus metrics and the ASGI middleware that records them (plus request IDs and headers)."""

from __future__ import annotations

import logging
import re
import time
import uuid
from typing import Any

from prometheus_client import (
    GC_COLLECTOR,
    PLATFORM_COLLECTOR,
    PROCESS_COLLECTOR,
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
)
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from autoswe.logging_config import request_id_var

REGISTRY = CollectorRegistry(auto_describe=True)
for _collector in (PROCESS_COLLECTOR, PLATFORM_COLLECTOR, GC_COLLECTOR):
    REGISTRY.register(_collector)

HTTP_REQUESTS = Counter(
    "autoswe_http_requests_total",
    "HTTP requests by route template and status code",
    ["method", "route", "status"],
    registry=REGISTRY,
)
HTTP_LATENCY = Histogram(
    "autoswe_http_request_duration_seconds",
    "HTTP request latency (time to response start for streams)",
    ["method", "route"],
    registry=REGISTRY,
)
JOBS_SUBMITTED = Counter(
    "autoswe_jobs_submitted_total", "Jobs accepted by the API", ["provider"], registry=REGISTRY
)
JOBS_FINISHED = Counter(
    "autoswe_jobs_finished_total",
    "Jobs that reached a terminal state",
    ["provider", "status"],
    registry=REGISTRY,
)
JOBS_RUNNING = Gauge(
    "autoswe_jobs_running", "Jobs currently executing in this process", registry=REGISTRY
)
JOBS_QUEUED = Gauge(
    "autoswe_jobs_queued", "Jobs waiting in the queue (last observed)", registry=REGISTRY
)
STAGE_DURATION = Histogram(
    "autoswe_stage_duration_seconds",
    "Wall-clock duration of each pipeline stage",
    ["stage", "provider", "outcome"],
    buckets=(0.01, 0.05, 0.1, 0.5, 1, 5, 15, 30, 60, 120, 300, 600),
    registry=REGISTRY,
)
LLM_TOKENS = Counter(
    "autoswe_llm_tokens_total",
    "Tokens consumed by LLM providers",
    ["provider", "direction"],
    registry=REGISTRY,
)
VALIDATION_ISSUES = Counter(
    "autoswe_validation_issues_total",
    "Issues found by deterministic validation of generated projects",
    ["severity"],
    registry=REGISTRY,
)

_SAFE_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
_access_log = logging.getLogger("autoswe.access")

SECURITY_HEADERS: tuple[tuple[bytes, bytes], ...] = (
    (b"x-content-type-options", b"nosniff"),
    (b"x-frame-options", b"DENY"),
    (b"referrer-policy", b"strict-origin-when-cross-origin"),
    (
        b"content-security-policy",
        b"default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
        b"img-src 'self' data:; font-src 'self' data:; connect-src 'self'; "
        b"frame-ancestors 'none'; base-uri 'self'; form-action 'self'",
    ),
)
_DOCS_PATHS = ("/api/docs", "/api/redoc")


class ObservabilityMiddleware:
    """Request IDs, access logs, latency/count metrics and security headers (pure ASGI, so
    streaming responses are not buffered)."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = dict(scope.get("headers") or [])
        incoming = headers.get(b"x-request-id", b"").decode("latin-1")
        request_id = incoming if _SAFE_REQUEST_ID.match(incoming) else uuid.uuid4().hex
        token = request_id_var.set(request_id)
        start = time.perf_counter()
        status_holder: dict[str, Any] = {"status": 500}
        is_docs = str(scope.get("path", "")).startswith(_DOCS_PATHS)

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                status_holder["status"] = message["status"]
                response_headers = list(message.get("headers", []))
                response_headers.append((b"x-request-id", request_id.encode()))
                existing = {name.lower() for name, _ in response_headers}
                for name, value in SECURITY_HEADERS:
                    # Swagger UI loads its assets from a CDN; keep the strict CSP elsewhere.
                    if name == b"content-security-policy" and is_docs:
                        continue
                    if name not in existing:
                        response_headers.append((name, value))
                message["headers"] = response_headers
                status_holder["latency"] = time.perf_counter() - start
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            route = scope.get("route")
            template = getattr(route, "path", None) or "unmatched"
            method = scope.get("method", "GET")
            status = str(status_holder["status"])
            latency = status_holder.get("latency", time.perf_counter() - start)
            HTTP_REQUESTS.labels(method, template, status).inc()
            HTTP_LATENCY.labels(method, template).observe(latency)
            if template not in {"/healthz", "/readyz", "/metrics"}:
                _access_log.info(
                    "request",
                    extra={
                        "method": method,
                        "path": scope.get("path"),
                        "route": template,
                        "status": int(status),
                        "duration_ms": round(latency * 1000, 2),
                    },
                )
            request_id_var.reset(token)
