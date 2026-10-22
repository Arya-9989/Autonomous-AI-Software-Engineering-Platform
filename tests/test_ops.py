"""Health checks, metrics, logging, headers, migrations and the SPA fallback."""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from alembic import command

from autoswe.logging_config import JsonFormatter, job_id_var, request_id_var
from autoswe.migrations.runner import alembic_config, upgrade_to_head
from tests.conftest import AppHarness, HarnessFactory


async def test_liveness_and_readiness(harness: AppHarness) -> None:
    assert (await harness.client.get("/healthz")).json() == {"status": "ok"}
    ready = await harness.client.get("/readyz")
    assert ready.status_code == 200
    assert ready.json() == {"status": "ok", "checks": {"database": "ok", "worker": "ok"}}


class _UnreachableDatabase:
    @asynccontextmanager
    async def session(self) -> AsyncIterator[None]:
        raise ConnectionRefusedError("database is down")
        yield  # type: ignore[unreachable]  # makes this an async generator


async def test_readiness_fails_when_database_is_unreachable(
    harness_factory: HarnessFactory,
) -> None:
    harness = await harness_factory(worker_enabled=False)
    services = harness.app.state.services
    original, services.db = services.db, _UnreachableDatabase()
    try:
        response = await harness.client.get("/readyz")
    finally:
        services.db = original
    assert response.status_code == 503
    assert response.json() == {
        "status": "unavailable",
        "checks": {"database": "error: ConnectionRefusedError"},
    }
    assert (await harness.client.get("/healthz")).status_code == 200  # liveness unaffected


async def test_metrics_expose_pipeline_and_http_series(harness: AppHarness) -> None:
    job_id = (await harness.submit())["id"]
    await harness.wait_for(job_id, {"succeeded"})
    await harness.client.get("/api/v1/jobs/does-not-exist", headers={"X-API-Key": "nope"})

    body = (await harness.client.get("/metrics")).text
    assert 'autoswe_jobs_submitted_total{provider="offline"}' in body
    assert 'autoswe_jobs_finished_total{provider="offline",status="succeeded"}' in body
    assert (
        'autoswe_stage_duration_seconds_count{outcome="ok",provider="offline",stage="review"}'
        in body
    )
    assert 'route="/api/v1/jobs/{job_id}",status="401"' in body  # templated, not raw paths
    assert "process_cpu_seconds_total" in body or "python_info" in body


async def test_metrics_can_be_disabled(harness_factory: HarnessFactory) -> None:
    harness = await harness_factory(metrics_enabled=False, worker_enabled=False)
    assert (await harness.client.get("/metrics")).status_code == 404


async def test_request_id_and_security_headers(harness: AppHarness) -> None:
    response = await harness.client.get("/healthz", headers={"X-Request-ID": "abc-123"})
    assert response.headers["x-request-id"] == "abc-123"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["cross-origin-opener-policy"] == "same-origin"
    assert "camera=()" in response.headers["permissions-policy"]
    csp = response.headers["content-security-policy"]
    assert "default-src 'self'" in csp
    assert "script-src 'self';" in csp  # no frontend built here: no inline script hashes
    assert "object-src 'none'" in csp

    generated = await harness.client.get("/healthz", headers={"X-Request-ID": "bad id\nx"})
    assert generated.headers["x-request-id"] != "bad id\nx"
    assert len(generated.headers["x-request-id"]) == 32

    # Swagger UI gets its own policy: its CDN and inline bootstrap script, nothing else.
    docs = await harness.client.get("/api/docs")
    assert docs.status_code == 200
    docs_csp = docs.headers["content-security-policy"]
    assert "default-src 'none'" in docs_csp
    assert "script-src 'unsafe-inline' https://cdn.jsdelivr.net;" in docs_csp
    assert "frame-ancestors 'none'" in docs_csp


def test_json_logs_carry_request_and_job_context() -> None:
    record = logging.makeLogRecord({"msg": "hello %s", "args": ("world",), "levelname": "INFO",
                                    "name": "t", "stage": "review"})  # fmt: skip
    record.request_id = None
    token_r, token_j = request_id_var.set("req-1"), job_id_var.set("job-1")
    try:
        from autoswe.logging_config import ContextFilter

        ContextFilter().filter(record)
    finally:
        request_id_var.reset(token_r)
        job_id_var.reset(token_j)
    payload = json.loads(JsonFormatter().format(record))
    assert payload["msg"] == "hello world"
    assert payload["request_id"] == "req-1"
    assert payload["job_id"] == "job-1"
    assert payload["stage"] == "review"


def test_migrations_match_the_models(tmp_path: Path) -> None:
    url = f"sqlite+aiosqlite:///{(tmp_path / 'm.db').as_posix()}"
    upgrade_to_head(url)
    command.check(alembic_config(url))  # raises if models drifted from migrations
    command.downgrade(alembic_config(url), "base")
    upgrade_to_head(url)


async def test_spa_fallback_serves_index(harness_factory: HarnessFactory, tmp_path: Path) -> None:
    static = tmp_path / "dist"
    (static / "assets").mkdir(parents=True)
    (static / "index.html").write_text("<!doctype html><title>ui</title>")
    (static / "assets" / "app.js").write_text("console.log(1)")
    (static / "favicon.svg").write_text("<svg/>")
    harness = await harness_factory(static_dir=static, worker_enabled=False)

    for path in ("/", "/app", "/app/jobs/123", "/jobs/123"):
        response = await harness.client.get(path)
        assert response.status_code == 200
        assert "<title>ui</title>" in response.text
    unknown = await harness.client.get("/new")  # not a client route: a real 404, same page
    assert unknown.status_code == 404
    assert "<title>ui</title>" in unknown.text
    assert (await harness.client.get("/assets/app.js")).text == "console.log(1)"
    assert (await harness.client.get("/favicon.svg")).text == "<svg/>"
    assert (await harness.client.get("/api/v1/nope")).status_code == 404
    (tmp_path / "secret.txt").write_text("top secret")
    for probe in ("/..%2fsecret.txt", "/%2e%2e/secret.txt", "/assets/..%2f..%2fsecret.txt"):
        assert "top secret" not in (await harness.client.get(probe)).text
