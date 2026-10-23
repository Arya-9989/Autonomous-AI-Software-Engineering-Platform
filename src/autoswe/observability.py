"""Prometheus metrics and the ASGI middleware that records them (plus request IDs and headers)."""

from __future__ import annotations

import logging
import re
import time
import uuid
from collections.abc import Iterable
from typing import Any

from opentelemetry import trace
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
JOB_RETRIES = Counter(
    "autoswe_job_retries_total",
    "Jobs requeued to resume at a stage: transient (automatic, after a provider error) or manual",
    ["reason"],
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
LLM_COST = Counter(
    "autoswe_llm_cost_usd_total",
    "Cost of LLM calls priced from the pricing table (unpriced models excluded)",
    ["provider", "model"],
    registry=REGISTRY,
)
BUDGET_REJECTIONS = Counter(
    "autoswe_budget_rejections_total",
    "Jobs refused at submission or stopped while running by a token budget",
    ["where"],
    registry=REGISTRY,
)
GITHUB_PUBLISH = Counter(
    "autoswe_github_publish_total",
    "Attempts to publish a generated project to GitHub, by outcome",
    ["outcome"],
    registry=REGISTRY,
)
VALIDATION_ISSUES = Counter(
    "autoswe_validation_issues_total",
    "Issues found by deterministic validation of generated projects",
    ["severity"],
    registry=REGISTRY,
)

_SAFE_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
_UNTRACED = frozenset({"/healthz", "/readyz", "/metrics"})


def untraced(scope: Any) -> bool:
    """For ``FastAPI(telemetry={"exclude": ...})``: probes and scrapes make no spans."""
    return scope.get("path") in _UNTRACED


_access_log = logging.getLogger("autoswe.access")

SECURITY_HEADERS: tuple[tuple[bytes, bytes], ...] = (
    (b"x-content-type-options", b"nosniff"),
    (b"x-frame-options", b"DENY"),
    (b"referrer-policy", b"strict-origin-when-cross-origin"),
    (b"cross-origin-opener-policy", b"same-origin"),
    (b"permissions-policy", b"camera=(), microphone=(), geolocation=(), payment=(), usb=()"),
)
_DOCS_PATHS = ("/api/docs", "/api/redoc")
# Swagger UI is a page FastAPI renders with one inline script and assets from jsDelivr.
DOCS_CSP = (
    b"default-src 'none'; script-src 'unsafe-inline' https://cdn.jsdelivr.net; "
    b"style-src 'unsafe-inline' https://cdn.jsdelivr.net; img-src 'self' data: "
    b"https://fastapi.tiangolo.com; connect-src 'self'; frame-ancestors 'none'; "
    b"base-uri 'none'; form-action 'none'"
)


def content_security_policy(script_hashes: Iterable[str] = ()) -> bytes:
    """The app's CSP. ``script_hashes`` (``sha256-...``) admit the UI's inline scripts, which run
    before the first paint (theme, preloads), without allowing inline script in general."""
    sources = " ".join(["'self'", *(f"'{digest}'" for digest in script_hashes)])
    return (
        f"default-src 'self'; script-src {sources}; style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; font-src 'self' data:; connect-src 'self'; object-src 'none'; "
        "frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
    ).encode()


class ObservabilityMiddleware:
    """Request IDs, access logs, latency/count metrics and security headers (pure ASGI, so
    streaming responses are not buffered)."""

    def __init__(self, app: ASGIApp, *, csp: bytes | None = None) -> None:
        self.app = app
        self.csp = csp or content_security_policy()

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
                csp = DOCS_CSP if is_docs else self.csp
                for name, value in (*SECURITY_HEADERS, (b"content-security-policy", csp)):
                    if name not in existing:
                        response_headers.append((name, value))
                message["headers"] = response_headers
                status_holder["latency"] = time.perf_counter() - start
            await send(message)

        method = scope.get("method", "GET")
        path = str(scope.get("path", ""))
        # FastAPI's native telemetry wraps the app in a SERVER span (W3C traceparent included);
        # tag it so a trace can be found from the X-Request-ID a client saw.
        span = trace.get_current_span()
        if span.is_recording():
            span.set_attribute("autoswe.request_id", request_id)
        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            route = scope.get("route")
            template = getattr(route, "path", None) or "unmatched"
            self._record(method, template, status_holder, start, path)
            request_id_var.reset(token)

    @staticmethod
    def _record(
        method: str, template: str, status_holder: dict[str, Any], start: float, path: str
    ) -> None:
        status = str(status_holder["status"])
        latency = status_holder.get("latency", time.perf_counter() - start)
        HTTP_REQUESTS.labels(method, template, status).inc()
        HTTP_LATENCY.labels(method, template).observe(latency)
        if template not in _UNTRACED:
            _access_log.info(
                "request",
                extra={
                    "method": method,
                    "path": path,
                    "route": template,
                    "status": int(status),
                    "duration_ms": round(latency * 1000, 2),
                },
            )
