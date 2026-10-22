"""OpenTelemetry tracing: one span per HTTP request, job, agent stage, model call and sandbox run.

Configuration is the standard OpenTelemetry environment, nothing AutoSWE-specific:

* ``OTEL_EXPORTER_OTLP_ENDPOINT`` / ``OTEL_EXPORTER_OTLP_TRACES_ENDPOINT`` - an OTLP/HTTP
  (protobuf) endpoint, e.g. ``http://jaeger:4318``; setting one turns export on;
* ``OTEL_TRACES_EXPORTER`` - ``otlp``, ``console`` or ``none`` (comma-separated);
* ``OTEL_SERVICE_NAME``, ``OTEL_RESOURCE_ATTRIBUTES``, ``OTEL_TRACES_SAMPLER``,
  ``OTEL_EXPORTER_OTLP_HEADERS``, ``OTEL_BSP_*`` and ``OTEL_SDK_DISABLED`` as documented by
  OpenTelemetry.

With none of them set, no tracer provider is installed and every span below is the API's no-op
span. The Anthropic SDK emits its own ``anthropic.messages.create`` spans (metadata only: no
prompts or completions) through the same provider, so they nest under the model-call spans here.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping
from typing import Any

from opentelemetry import propagate, trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import SpanProcessor, TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter
from opentelemetry.trace import Span, Status, StatusCode

from autoswe import __version__

logger = logging.getLogger(__name__)

TRACER_NAME = "autoswe"


def tracer() -> trace.Tracer:
    return trace.get_tracer(TRACER_NAME, __version__)


def _exporters(environ: Mapping[str, str]) -> list[str]:
    if environ.get("OTEL_SDK_DISABLED", "").strip().lower() == "true":
        return []
    configured = environ.get("OTEL_TRACES_EXPORTER")
    if configured is None or not configured.strip():
        endpoint = environ.get("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT") or environ.get(
            "OTEL_EXPORTER_OTLP_ENDPOINT"
        )
        return ["otlp"] if endpoint and endpoint.strip() else []
    names = [name.strip().lower() for name in configured.split(",") if name.strip()]
    return [name for name in names if name != "none"]


def build_tracer_provider(
    default_service: str, environ: Mapping[str, str] | None = None
) -> TracerProvider | None:
    """A provider for what the environment asks for, or ``None`` when tracing is off."""
    env = os.environ if environ is None else environ
    names = _exporters(env)
    if not names:
        return None
    processors: list[SpanProcessor] = []
    for name in names:
        if name == "otlp":
            protocol = env.get("OTEL_EXPORTER_OTLP_TRACES_PROTOCOL") or env.get(
                "OTEL_EXPORTER_OTLP_PROTOCOL", "http/protobuf"
            )
            if protocol != "http/protobuf":
                logger.warning(
                    "only the http/protobuf OTLP protocol is installed; using it",
                    extra={"requested": protocol},
                )
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import (  # noqa: PLC0415
                OTLPSpanExporter,
            )

            processors.append(BatchSpanProcessor(OTLPSpanExporter()))
        elif name == "console":
            processors.append(BatchSpanProcessor(ConsoleSpanExporter()))
        else:
            logger.warning("unknown OTEL_TRACES_EXPORTER entry ignored", extra={"value": name})
    if not processors:
        return None
    service = env.get("OTEL_SERVICE_NAME", "").strip() or default_service
    provider = TracerProvider(
        resource=Resource.create({"service.name": service, "service.version": __version__})
    )
    for processor in processors:
        provider.add_span_processor(processor)
    return provider


_installed: TracerProvider | None = None


def configure_tracing(default_service: str = "autoswe") -> TracerProvider | None:
    """Install the provider globally, once per process (OpenTelemetry allows only one).

    Returns the provider so the caller can flush it at shutdown; ``None`` when tracing is off.
    """
    global _installed  # noqa: PLW0603 - the process-wide provider
    if _installed is not None:
        return _installed
    provider = build_tracer_provider(default_service)
    if provider is None:
        return None
    trace.set_tracer_provider(provider)
    _installed = provider
    logger.info("tracing enabled", extra={"service": default_service})
    return provider


def current_context_carrier() -> dict[str, str]:
    """The current span's W3C trace context, to persist with work that runs later."""
    carrier: dict[str, str] = {}
    propagate.inject(carrier)
    return carrier


def trace_id_of(carrier: Mapping[str, Any] | None) -> str | None:
    """The trace ID in a stored ``traceparent`` (``00-<trace>-<span>-<flags>``)."""
    parent = (carrier or {}).get("traceparent")
    if not isinstance(parent, str):
        return None
    parts = parent.split("-")
    return parts[1] if len(parts) == 4 and len(parts[1]) == 32 else None


def mark_error(span: Span, error: BaseException | str, error_type: str | None = None) -> None:
    message = str(error)
    span.set_status(Status(StatusCode.ERROR, message[:500]))
    span.set_attribute("error.type", error_type or type(error).__name__)
    if isinstance(error, BaseException):
        span.record_exception(error)
