"""OpenTelemetry spans for requests, jobs, stages, model calls and sandbox runs.

A process can install one global tracer provider; this module installs an SDK provider with an
in-memory exporter (unless one is already installed) and reads the finished spans back.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from typing import Any

import anthropic
import httpx2
import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import SpanKind, StatusCode

from autoswe.llm.anthropic_provider import AnthropicProvider, http_event_hooks
from autoswe.logging_config import ContextFilter
from autoswe.metering import MeteredProvider
from autoswe.pricing import PricingTable
from autoswe.sandbox.backends import RemoteSandbox
from autoswe.sandbox.models import SandboxResult, SandboxStatus
from autoswe.tracing import build_tracer_provider, trace_id_of, tracer
from tests import sandbox_projects as projects
from tests.conftest import ALICE, LIBRARY_SPEC, HarnessFactory
from tests.fakes import PricedProvider, registry_with
from tests.test_anthropic_wire import REQUEST, Transport, _success_body

EXPORTER = InMemorySpanExporter()
if not isinstance(trace.get_tracer_provider(), TracerProvider):
    _provider = TracerProvider()
    _provider.add_span_processor(SimpleSpanProcessor(EXPORTER))
    trace.set_tracer_provider(_provider)
else:  # another module installed one first: attach the exporter to it
    trace.get_tracer_provider().add_span_processor(SimpleSpanProcessor(EXPORTER))  # type: ignore[attr-defined]


@pytest.fixture(autouse=True)
def spans() -> Iterator[InMemorySpanExporter]:
    EXPORTER.clear()
    yield EXPORTER
    EXPORTER.clear()


def _named(prefix: str) -> list[ReadableSpan]:
    return [s for s in EXPORTER.get_finished_spans() if s.name.startswith(prefix)]


def _one(name: str) -> ReadableSpan:
    (span,) = [s for s in EXPORTER.get_finished_spans() if s.name == name]
    return span


def _attrs(span: ReadableSpan) -> dict[str, Any]:
    return dict(span.attributes or {})


# --- configuration from the standard environment --------------------------------------------------


def _processors(provider: TracerProvider) -> list[Any]:
    return list(provider._active_span_processor._span_processors)


@pytest.mark.parametrize(
    "environ",
    [
        {},
        {"OTEL_TRACES_EXPORTER": "none", "OTEL_EXPORTER_OTLP_ENDPOINT": "http://x:4318"},
        {"OTEL_SDK_DISABLED": "true", "OTEL_EXPORTER_OTLP_ENDPOINT": "http://x:4318"},
        {"OTEL_EXPORTER_OTLP_ENDPOINT": "  "},
        {"OTEL_TRACES_EXPORTER": "zipkin-but-not-installed"},
    ],
)
def test_tracing_is_a_no_op_unless_the_environment_asks_for_export(
    environ: dict[str, str],
) -> None:
    assert build_tracer_provider("autoswe", environ) is None


def test_an_otlp_endpoint_turns_on_otlp_http_export() -> None:
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

    provider = build_tracer_provider(
        "autoswe", {"OTEL_EXPORTER_OTLP_ENDPOINT": "http://jaeger:4318"}
    )
    assert provider is not None
    (processor,) = _processors(provider)
    assert isinstance(processor.span_exporter, OTLPSpanExporter)
    assert provider.resource.attributes["service.name"] == "autoswe"
    provider.shutdown()


def test_service_name_and_exporter_list_come_from_the_environment(
    caplog: pytest.LogCaptureFixture,
) -> None:
    environ = {"OTEL_TRACES_EXPORTER": "console,otlp,bogus", "OTEL_SERVICE_NAME": "autoswe-eu",
               "OTEL_EXPORTER_OTLP_PROTOCOL": "grpc"}  # fmt: skip
    with caplog.at_level(logging.WARNING):
        provider = build_tracer_provider("autoswe", environ)
    assert provider is not None
    assert len(_processors(provider)) == 2
    assert provider.resource.attributes["service.name"] == "autoswe-eu"
    assert "only the http/protobuf OTLP protocol" in caplog.text
    assert "unknown OTEL_TRACES_EXPORTER" in caplog.text
    provider.shutdown()


def test_trace_id_of_a_stored_context() -> None:
    parent = "00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01"
    assert trace_id_of({"traceparent": parent}) == "0af7651916cd43dd8448eb211c80319c"
    assert trace_id_of({}) is None and trace_id_of(None) is None
    assert trace_id_of({"traceparent": "garbage"}) is None


# --- a job, end to end ----------------------------------------------------------------------------


async def test_a_job_is_one_trace_from_request_to_model_calls(
    harness_factory: HarnessFactory,
) -> None:
    harness = await harness_factory(registry=registry_with(PricedProvider()))
    response = await harness.client.post(
        "/api/v1/jobs", json={"spec": LIBRARY_SPEC},
        headers={**ALICE, "X-Request-ID": "req-trace-1"},
    )  # fmt: skip
    job = response.json()
    detail = await harness.wait_for(job["id"], {"succeeded"})

    request = _one("POST /api/v1/jobs")
    assert request.kind is SpanKind.SERVER
    assert _attrs(request)["http.route"] == "/api/v1/jobs"
    assert _attrs(request)["http.response.status_code"] == 202
    assert _attrs(request)["autoswe.request_id"] == "req-trace-1"

    run = _one("autoswe.job")
    assert run.kind is SpanKind.CONSUMER
    assert run.context.trace_id == request.context.trace_id  # one trace across the queue
    assert run.parent is not None and run.parent.span_id == request.context.span_id
    attrs = _attrs(run)
    assert attrs["autoswe.job.id"] == job["id"] and attrs["autoswe.request_id"] == "req-trace-1"
    assert attrs["autoswe.job.status"] == "succeeded"
    assert attrs["gen_ai.usage.input_tokens"] == 5_000 and attrs["autoswe.cost_usd"] == 0.0291
    assert detail["trace_id"] == f"{run.context.trace_id:032x}"
    assert detail["request_id"] == "req-trace-1"

    stages = _named("autoswe.stage ")
    assert [s.name for s in sorted(stages, key=lambda s: s.start_time or 0)] == [
        "autoswe.stage analyze", "autoswe.stage architect", "autoswe.stage implement",
        "autoswe.stage devops", "autoswe.stage review",
    ]  # fmt: skip
    for stage in stages:
        assert stage.parent is not None and stage.parent.span_id == run.context.span_id
        assert _attrs(stage)["gen_ai.usage.output_tokens"] == 200
        assert _attrs(stage)["autoswe.cost_usd"] == 0.00582

    calls = _named("autoswe.llm ")
    assert len(calls) == 5
    analyst = _one("autoswe.llm analyst")
    analyze = _one("autoswe.stage analyze")
    assert analyst.parent is not None and analyst.parent.span_id == analyze.context.span_id
    call = _attrs(analyst)
    assert call["autoswe.agent"] == "analyst" and call["autoswe.effort"] == "medium"
    assert call["gen_ai.request.model"] == "claude-opus-5-5"
    assert call["gen_ai.usage.input_tokens"] == 1_000
    assert call["gen_ai.usage.cache_read.input_tokens"] == 600
    assert call["gen_ai.usage.cache_write.input_tokens"] == 100
    assert call["autoswe.cost_usd"] == 0.00582 and call["autoswe.latency_ms"] >= 0
    assert call["autoswe.llm.billed_attempts"] == 1


async def test_an_incoming_traceparent_is_continued(harness_factory: HarnessFactory) -> None:
    harness = await harness_factory(worker_enabled=False)
    parent = "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"
    response = await harness.client.post(
        "/api/v1/jobs", json={"spec": LIBRARY_SPEC}, headers={**ALICE, "traceparent": parent}
    )
    assert response.json()["trace_id"] == "4bf92f3577b34da6a3ce929d0e0e4736"


async def test_probes_are_not_traced(harness_factory: HarnessFactory) -> None:
    harness = await harness_factory(worker_enabled=False)
    for path in ("/healthz", "/readyz", "/metrics"):
        await harness.client.get(path)
    await harness.client.get("/api/v1/info")
    assert [s.name for s in EXPORTER.get_finished_spans()] == ["GET /api/v1/info"]


async def test_a_budget_stop_marks_the_stage_and_the_job_as_errors(
    harness_factory: HarnessFactory,
) -> None:
    harness = await harness_factory(
        registry=registry_with(PricedProvider()),
        token_budget_default=2_000,
        job_token_reservation=1_000,
    )
    job = await harness.submit()
    await harness.wait_for(job["id"], {"failed"})
    stage = _one("autoswe.stage architect")
    assert stage.status.status_code is StatusCode.ERROR
    assert _attrs(stage)["error.type"] == "token_budget_exceeded"
    call = _one("autoswe.llm architect")
    assert call.status.status_code is StatusCode.ERROR
    assert any(event.name == "exception" for event in call.events)
    run = _one("autoswe.job")
    assert run.status.status_code is StatusCode.ERROR
    assert _attrs(run)["autoswe.job.status"] == "failed"


async def test_sandbox_runs_and_repair_attempts_are_spans(
    harness_factory: HarnessFactory,
) -> None:
    from tests.fakes import ScriptedProvider
    from tests.test_sandbox_pipeline import FAILED, PASSED, ScriptedSandbox, _repair_editing

    provider = ScriptedProvider({"coder.repair": _repair_editing("app/routers/books.py", "#")})
    harness = await harness_factory(registry=registry_with(provider))
    harness.app.state.orchestrator._sandbox = ScriptedSandbox(FAILED, PASSED)
    job = await harness.submit()
    await harness.wait_for(job["id"], {"succeeded"})

    runs = sorted(_named("autoswe.sandbox.run"), key=lambda s: s.start_time or 0)
    assert [_attrs(r)["autoswe.sandbox.status"] for r in runs] == ["failed", "passed"]
    assert [_attrs(r)["autoswe.repair.round"] for r in runs] == [0, 1]
    assert _attrs(runs[0])["autoswe.tests.failed"] == 1
    review = _one("autoswe.stage review")
    assert all(r.parent is not None and r.parent.span_id == review.context.span_id for r in runs)
    repair = _one("autoswe.llm coder.repair")
    assert _attrs(repair)["autoswe.repair.kind"] == "tests"
    assert _attrs(repair)["autoswe.repair.round"] == 1


async def test_a_sandbox_error_is_an_error_span() -> None:
    from autoswe.pipeline.agents import PipelineState, _traced_sandbox_run
    from tests.test_sandbox_pipeline import ScriptedSandbox

    error = SandboxResult(status=SandboxStatus.ERROR, backend="x", isolation="y", message="down")
    state = PipelineState(spec=LIBRARY_SPEC, sandbox=ScriptedSandbox(error))
    await _traced_sandbox_run(state, 0)
    span = _one("autoswe.sandbox.run")
    assert span.status.status_code is StatusCode.ERROR
    assert _attrs(span)["error.type"] == "sandbox_error"


# --- propagation and correlation -----------------------------------------------------------------


async def test_the_remote_sandbox_carries_the_trace_to_the_runner() -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen["traceparent"] = request.headers.get("traceparent", "")
        ok = SandboxResult(status=SandboxStatus.PASSED, backend="docker", isolation="i")
        return httpx2.Response(200, json=ok.model_dump(mode="json"))

    remote = RemoteSandbox("http://runner", "t" * 20, timeout_s=5,
                           transport=httpx2.MockTransport(handler))  # fmt: skip
    with tracer().start_as_current_span("job") as span:
        await remote.run(projects.passing())
    assert f"{span.get_span_context().trace_id:032x}" in seen["traceparent"]


def test_log_records_carry_the_trace_and_span_ids() -> None:
    record = logging.makeLogRecord({"msg": "hello"})
    with tracer().start_as_current_span("work") as span:
        ContextFilter().filter(record)
    context = span.get_span_context()
    assert getattr(record, "trace_id", None) == f"{context.trace_id:032x}"
    assert getattr(record, "span_id", None) == f"{context.span_id:016x}"
    outside = logging.makeLogRecord({"msg": "hello"})
    ContextFilter().filter(outside)
    assert getattr(outside, "trace_id", "unset") is None


# --- retries are visible --------------------------------------------------------------------------


async def test_sdk_retries_are_counted_on_the_model_call_span() -> None:
    overloaded = httpx2.Response(
        529, headers={"retry-after-ms": "1", "content-type": "application/json"},
        content=json.dumps({"type": "error", "error": {"type": "overloaded_error",
                                                         "message": "busy"}}).encode(),
    )  # fmt: skip
    transport = Transport(lambda _: overloaded, _success_body())
    client = anthropic.AsyncAnthropic(
        api_key="sk-ant-test-key", max_retries=2,
        http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(transport),
                                       event_hooks=http_event_hooks()),
    )  # fmt: skip
    provider = AnthropicProvider(model="claude-opus-5-5", max_tokens=64_000, client=client)
    metered = MeteredProvider(provider, PricingTable.load())
    response = await metered.generate(REQUEST)
    assert response.http_attempts == 2
    call = _attrs(_one("autoswe.llm reviewer"))
    assert call["autoswe.llm.http_attempts"] == 2 and call["autoswe.llm.retries"] == 1
    # 120 uncached x $4 + 900 cache reads x $0.20 + 30 cache writes x $5 + 40 output x $20
    assert call["autoswe.cost_usd"] == round((480 + 180 + 150 + 800) / 1e6, 6)
    # The SDK's own span nests under ours: one request span covering both attempts.
    sdk = _one("anthropic.messages.create")  # the SDK's own span, metadata only
    assert sdk.kind is SpanKind.CLIENT
    ours = _one("autoswe.llm reviewer")
    assert sdk.parent is not None and sdk.parent.span_id == ours.context.span_id


async def test_failed_calls_record_their_attempts() -> None:
    denied = httpx2.Response(
        401, headers={"content-type": "application/json"},
        content=json.dumps({"type": "error", "error": {"type": "authentication_error",
                                                         "message": "bad key"}}).encode(),
    )  # fmt: skip
    client = anthropic.AsyncAnthropic(
        api_key="sk-ant-test-key", max_retries=0,
        http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(lambda _: denied),
                                       event_hooks=http_event_hooks()),
    )  # fmt: skip
    metered = MeteredProvider(
        AnthropicProvider(model="claude-opus-5-5", max_tokens=1_024, client=client),
        PricingTable.load(),
    )
    with pytest.raises(Exception, match="rejected the API key"):
        await metered.generate(REQUEST)
    call = _one("autoswe.llm reviewer")
    assert call.status.status_code is StatusCode.ERROR
    assert _attrs(call)["error.type"] == "LLMConfigurationError"
    assert _attrs(call)["autoswe.llm.http_attempts"] == 1
