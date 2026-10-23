# ADR 0004: OpenTelemetry tracing of agent steps

- Status: accepted
- Date: 2026-10-23

## Context

A job crosses an HTTP request, the database queue, a worker, five agents, several model calls,
repair rounds and sandbox runs in another service. Logs carry `request_id` and `job_id`, and
Prometheus has aggregates, but nothing shows one job's timeline with its latencies and tokens.

## Decision

- Use the OpenTelemetry API everywhere and install the SDK only when the **standard environment**
  asks for it: `OTEL_EXPORTER_OTLP_ENDPOINT` (or `..._TRACES_ENDPOINT`) turns on OTLP/HTTP export,
  `OTEL_TRACES_EXPORTER` chooses `otlp`, `console` or `none`, and `OTEL_SERVICE_NAME`,
  `OTEL_RESOURCE_ATTRIBUTES`, `OTEL_TRACES_SAMPLER`, `OTEL_BSP_*` and `OTEL_SDK_DISABLED` behave as
  OpenTelemetry documents. With nothing set, no provider is installed and every span is the API's
  no-op.
- Spans:
  - HTTP: FastAPI's native SERVER span per request (it already continues a W3C `traceparent`),
    tagged with `autoswe.request_id`; probes and `/metrics` are excluded.
  - `autoswe.job` (CONSUMER): the worker's run. Its parent is the submitting request's span: the
    request's trace context is stored with the job (migration `0004`) and extracted when a worker
    claims it, so one trace spans the queue. The job detail returns `trace_id` and `request_id`.
  - `autoswe.stage <stage>`: one per agent stage, with duration, files, tokens and cost.
  - `autoswe.llm <agent>`: one per model call, with the GenAI semantic-convention attributes
    (`gen_ai.request.model`, `gen_ai.response.model`, `gen_ai.usage.input_tokens` (cache
    included), `..output_tokens`, `..cache_read.input_tokens`, `..cache_write.input_tokens`) plus
    effort, latency, cost, billed attempts (server-side fallbacks), HTTP attempts and retries
    (counted with an httpx event hook on the SDK's client), and `autoswe.repair.kind` and
    `autoswe.repair.round` for repair calls. Errors set the span status and `error.type`.
  - `autoswe.sandbox.run`: one per sandbox attempt, with backend, status, limit hit, test counts
    and the repair round. `RemoteSandbox` sends `traceparent`, so the runner's SERVER span joins.
  - The Anthropic SDK emits its own `anthropic.messages.create` CLIENT span through the same
    provider; it nests under `autoswe.llm <agent>`. Its content mode stays the default
    (metadata only): prompts and generated code are not exported.
- Logs gain `trace_id` and `span_id` whenever a span is active.
- Compose has an optional Jaeger v2 service behind the `tracing` profile.

## Options considered

- Hand-rolled HTTP SERVER spans in our middleware: rejected once FastAPI's native telemetry was
  found to emit them already; two SERVER spans per request would double-count.
- A parent link instead of a parent for the job span: links are the textbook choice for queued
  work, but most trace viewers show a link as a separate trace. With one message per job, the
  messaging conventions allow the consumer span to be a child, which keeps the job in one trace.
- The gRPC exporter: heavier dependency; OTLP/HTTP works with every common collector.

## Consequences

- One new runtime dependency group (`opentelemetry-sdk`, `opentelemetry-exporter-otlp-proto-http`).
- Tests install an SDK provider with an in-memory exporter and assert the span tree, attributes,
  error statuses, propagation to the runner, log correlation and retry counting.
