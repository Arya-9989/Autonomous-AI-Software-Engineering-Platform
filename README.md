# Autonomous AI Software Engineering Platform

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/hero-dark.svg">
  <source media="(prefers-color-scheme: light)" srcset="docs/hero-light.svg">
  <img alt="AutoSWE: plain English in, deployable project out. The five-agent pipeline, all stages done; offline run on the library spec: 37 files, 0 errors, review 100/100." src="docs/hero-dark.svg">
</picture>

[![CI](https://github.com/Arya-9989/Autonomous-AI-Software-Engineering-Platform/actions/workflows/ci.yml/badge.svg)](https://github.com/Arya-9989/Autonomous-AI-Software-Engineering-Platform/actions/workflows/ci.yml)
![Python 3.12](https://img.shields.io/badge/python-3.12-3776ab)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

**AutoSWE** turns a natural-language specification into a reviewed, deployable repository.
A pipeline of five agents analyses requirements, designs the architecture, writes a FastAPI
service with tests, adds a Dockerfile, a GitHub Actions workflow, Kubernetes manifests and
Terraform, and then validates and reviews the result. Every file the agents produce is treated as
untrusted input: it is parsed with real parsers, checked against security and operability rules,
and its test suite is run in an isolated, network-less container whose failures go back to the
code generator for repair. Every model call is metered against per-principal token budgets,
priced, and traced with OpenTelemetry. Jobs run asynchronously with persisted, stage-by-stage progress that
streams to a live web UI over Server-Sent Events. The agents run on **Claude** (Anthropic API), or
on a deterministic **offline provider** that needs no API key, so tests, CI and the demo run anywhere.

> The platform **generates** infrastructure code. It never runs `terraform apply` or deploys
> anything to AWS for you. The generated Terraform is checked with `terraform validate` in CI.

<picture>
  <source srcset="docs/job-running.webp" type="image/webp">
  <img alt="A job running live: the five agents hand over one by one, events stream in, then the generated files open" src="docs/job-running.gif">
</picture>

<sub>A real job on the library spec, recorded from the studio with the offline provider and
`OFFLINE_DELAY_S=2` so each stage stays on screen long enough to see, played at 1.5× speed.
Animated WebP (479 KB), with a shorter GIF for clients that cannot show WebP.</sub>

## Features

- **Three target stacks** ([ADR 0007](docs/adr/0007-more-target-stacks.md)): Python (FastAPI +
  SQLAlchemy, the default), TypeScript on Node.js 24 and Go 1.27, chosen per job (`"stack"`).
  Each has its own prompts, offline templates, structural checks and sandbox image that runs its
  linter, type checker and tests (ruff/mypy/pytest, ESLint/`tsc`/`node --test`,
  gofmt/`go vet`/`go test`). The TypeScript and Go services use only their standard libraries and
  keep data in memory, which the architecture and review say plainly.
- **Entity relationships and migrations** ([ADR 0008](docs/adr/0008-entity-relationships-and-migrations.md)):
  the analyst finds many-to-one links ("members borrow books" makes a Loan belong to a Member and
  a Book; `Comment(post:Post, ...)` declares one). In every stack, each link becomes a required
  `<parent>_id` that must point at an existing parent, filters its lists, and cascades on delete.
  Generated Python projects own their schema with Alembic: an initial revision with foreign keys
  and indexes, `alembic upgrade head` before the server starts, and a test that the migrations
  upgrade, match the models and downgrade cleanly. That test runs in the sandbox on every job,
  and on PostgreSQL in CI.
- **Five-agent pipeline**: requirements analyst → architect → code generator → DevOps engineer →
  reviewer. Each agent produces a typed Pydantic artifact (requirements, architecture spec, file
  bundle, review report).
- **Pluggable LLM providers** behind one interface:
  - `anthropic`: Claude (`claude-opus-5-5` by default) with structured outputs (JSON schema from
    the artifact type), adaptive thinking with per-agent effort, a cacheable system prompt,
    streaming, and server-side refusal fallbacks.
  - `offline`: deterministic heuristics and templates. It extracts entities from the spec
    (`members borrow books` → `Member`, `Book`, or explicit `Book(title, year:int)` syntax) and
    renders a working FastAPI CRUD service.
- **Validation of generated output**:
  - Path-safety and size limits: absolute paths, `..`, `.git` in any letter case, and names
    that are unsafe once extracted on Windows or macOS are rejected; paths that differ only in
    case count as one file.
  - Every file is parsed: `ast` for Python, PyYAML, python-hcl2 for Terraform, `json`, `tomllib`.
  - Kubernetes rules: probes, resource limits, non-root, privileged containers.
  - Dockerfile rules: pinned base images, non-root user, health check.
  - GitHub Actions rules: triggers, `runs-on`, version-pinned actions.
  - A secret scanner, required-structure checks, and design-to-code traceability.
  - When validation fails, one bounded **repair round** goes back to the code generator.
- **Sandboxed test execution with a repair loop** ([ADR 0001](docs/adr/0001-sandboxed-test-execution.md)):
  - The reviewer runs the generated project's `ruff`, `mypy` and `pytest` in a container per run:
    no network, read-only root file system, size-capped tmpfs workspace, unprivileged user, all
    capabilities dropped, `no-new-privileges`, memory (no swap), CPU and pid limits, and a
    wall-clock timeout enforced from outside. Nothing from the host is mounted: the workspace
    streams in as a tar. An optional gVisor runtime (`SANDBOX_DOCKER_RUNTIME=runsc`) is supported
    but was not available on the machine this was built on.
  - Failures come back as data (test id, phase, error, traceback excerpt), and timeouts,
    out-of-memory kills and pid exhaustion are classified as outcomes. They go to the code
    generator as repair input for at most `SANDBOX_MAX_REPAIR_ROUNDS` rounds; every attempt is
    kept and served at `GET /api/v1/jobs/{id}/tests`.
  - Only a separate **sandbox runner** service holds the Docker socket. Its one endpoint takes
    files and nothing else (image, command and limits are its own configuration), behind a token,
    on an internal network. The API container has no Docker access.
- **Eval harness with a CI regression gate** ([ADR 0003](docs/adr/0003-eval-harness-with-recorded-responses.md)):
  six spec fixtures scored on objective checks (tests pass in the sandbox, ruff, mypy,
  expected endpoints actually served by the code, entities and fields, no invented entities),
  replayed from recorded responses so CI needs no key, with thresholds checked into the repo and a
  Markdown/JSON report. `--live` runs it against Claude; `autoswe eval record` refreshes the
  recordings. The checked-in recordings come from the offline provider and are labelled as such.
- **Per-principal token budgets and cost** ([ADR 0002](docs/adr/0002-token-budgets-and-cost.md)):
  input, output and cache tokens and their cost per stage, job and principal; budgets enforced
  server-side with compare-and-set reservations, so concurrent jobs cannot overspend (HTTP 402
  at submission, `token_budget_exceeded` mid-run); prices from a table in config.
- **Async jobs with durable state**: SQLAlchemy 2.0 (SQLite by default, PostgreSQL in compose and
  production), Alembic migrations, and a DB-backed queue with atomic claims. Heartbeats, a
  stale-job reaper, cancellation, and graceful requeue on shutdown make it safe to run several
  replicas.
- **Resumable jobs** ([ADR 0009](docs/adr/0009-resumable-jobs.md)): a stage that fails with a
  transient provider error (rate limit, overload, timeout, 5xx) requeues the job with exponential
  backoff, up to `JOB_TRANSIENT_RETRIES` times. The next attempt resumes at that stage, keeping
  the artifacts of the stages that finished, so they are not paid for twice. A failed job can be
  retried the same way (`POST /api/v1/jobs/{id}/retry`), and a job requeued by a worker shutdown
  keeps its finished stages too.
- **Live progress over SSE**: the persisted event log is replayed, then followed live. Clients resume
  with `Last-Event-ID`.
- **Outputs**: a downloadable zip (deterministic archive), a file browser, `ARCHITECTURE.md` with a
  Mermaid diagram and ADRs, and machine-readable `docs/architecture.json`.
- **Publish to GitHub** ([ADR 0005](docs/adr/0005-publish-to-github.md)): `POST
  /api/v1/jobs/{id}/publish` creates a new repository (private by default) in the operator's
  account or organisation, commits the project on an `autoswe/<job>` branch and opens a pull
  request. It never writes to an existing repository; the token is never logged or stored.
- **Observability**: Prometheus `/metrics` (HTTP latency by route template, jobs, stage durations,
  LLM tokens and cost, budget rejections, validation issues), structured JSON logs carrying
  `request_id`, `job_id`, `trace_id` and `span_id`, `/healthz` and `/readyz` probes, and
  **OpenTelemetry tracing** ([ADR 0004](docs/adr/0004-opentelemetry-tracing.md)): one trace per
  job, from the submitting request across the queue to a span per agent stage, model call
  (tokens, latency, cost, retries, repair round) and sandbox run, including the runner service.
- **Security**: API-key auth with jobs scoped to each principal (cross-tenant access returns 404),
  per-principal active-job limits, a strict CSP (the UI's inline boot script is admitted by its
  hash, nothing else inline), security headers, a non-root container, and no secrets in code.
- **Web UI** (React, TypeScript, Tailwind, Motion, Radix):
  - A product page at `/`, dark-first, built from a real offline run of the pipeline: a replay
    of the run on the five-agent rail, a pinned scroll story that shows how the pipeline works
    (each agent lights up and hands its typed artifact on, the real file tree grows as files are
    written, real code lines appear, and the reviewer ticks off every file with the parser that
    checks it), and an unedited view of the generated code. A test regenerates that data and
    fails on drift.
  - Motion that explains rather than decorates: the story is scrubbed by scroll on a CSS scroll
    timeline (where a browser lacks one, a few lines of script seek the same animations), the
    local nav's underline slides to the section in view, the validation numbers count up once, a
    job card morphs into its page (View Transitions), and pipeline stages spring as a job
    advances. All of it is progressive: the prerendered page is complete without JavaScript, and
    reduced motion gets final states.
  - The studio at `/app`: spec composer with examples, a live pipeline timeline and streaming
    activity log, file tree with Shiki highlighting, architecture (Mermaid), review and
    requirements tabs, a ⌘K / Ctrl+K command palette, toasts and accessible dialogs.
  - Light and dark themes that follow the OS (with a manual switch), reduced-motion support,
    designed loading, empty and error states, and layouts from 320px up.
  - Unknown paths get a real `404` status with the designed "Page not found" page; the server
    knows the client router's routes (`CLIENT_ROUTES` in `web.py`, kept in step by a test), so
    deep links to real pages still return `200`.
  - Fast on a phone: the product page and the studio's loading states are prerendered at build
    time and hydrated, assets are precompressed (Brotli, gzip) and cached as immutable, the
    interface fonts are subset, code is highlighted in a Web Worker, and everything not needed
    for the first paint loads on demand.
- **Deployment for the platform itself**: a Kustomize base and AWS Terraform (ECS Fargate, ALB,
  RDS, Secrets Manager). In Kubernetes the API and the queue workers are separate Deployments, and
  KEDA scales the workers on queue depth (queued plus running jobs, read from PostgreSQL); workers
  drain on shutdown and requeue what they cannot finish
  ([ADR 0006](docs/adr/0006-worker-deployment-with-keda.md)).
- **CLI**: `autoswe generate "<spec>" --out ./project [--sandbox docker|local]` runs the whole
  pipeline locally; `autoswe eval run|record` and `autoswe usage` cover evals and the cost ledger.

<picture>
  <source srcset="docs/story-scroll.webp" type="image/webp">
  <img alt="The product page's pinned story, scrolled: the five agents light up in turn, the real file tree grows, real code lines appear and the reviewer ticks off every file" src="docs/story-scroll.gif">
</picture>

<sub>The product page's "How it works" story, recorded at 1440×900 while scrolling at a steady pace
(6 frames a second here). Every file, line and number is from a real offline run.</sub>

<table>
  <tr>
    <td><img src="docs/screenshots/landing-hero-dark.png" alt="Product page hero: Plain English in. Deployable project out."></td>
    <td><img src="docs/screenshots/landing-story-dark.png" alt="How it works, pinned: the code generator at work, its files growing in the real tree and app/routers/books.py being written"></td>
  </tr>
  <tr>
    <td><img src="docs/screenshots/landing-output-dark.png" alt="Output: unedited generated code in a highlighted viewer"></td>
    <td><img src="docs/screenshots/landing-highlights-light.png" alt="Highlights carousel in the light theme, with pager and pause control"></td>
  </tr>
  <tr>
    <td><img src="docs/screenshots/job-running-dark.png" alt="Studio: a job running, with the live timeline and activity stream"></td>
    <td><img src="docs/screenshots/job-files-dark.png" alt="Studio: generated file tree and highlighted code"></td>
  </tr>
  <tr>
    <td><img src="docs/screenshots/job-architecture-light.png" alt="Studio: architecture tab with the component diagram, light theme"></td>
    <td><img src="docs/screenshots/job-review-dark.png" alt="Studio: review score, validation and findings"></td>
  </tr>
  <tr>
    <td><img src="docs/screenshots/new-project-light.png" alt="Studio: spec composer with an example filled in, light theme"></td>
    <td><img src="docs/screenshots/command-palette-dark.png" alt="Studio: command palette"></td>
  </tr>
</table>

<p align="center">
  <img src="docs/screenshots/mobile-landing-dark.png" alt="Mobile: product page" width="260">
  &nbsp;
  <img src="docs/screenshots/mobile-job-dark.png" alt="Mobile: job view" width="260">
</p>

All screenshots and the recording are of the real application running the offline provider.

## Architecture

```mermaid
flowchart LR
    UI["React UI<br/>(served by FastAPI)"] -- "REST + SSE" --> API
    CLI["autoswe generate"] --> PIPE
    subgraph Service["autoswe service (one container, N replicas)"]
        API["FastAPI API<br/>auth · validation · metrics"] -- "enqueue (jobs table)" --> DB
        W["Worker<br/>atomic claim · heartbeat"] -- "claim" --> DB
        W --> PIPE
        subgraph PIPE["Agent pipeline"]
            direction LR
            A1["Requirements<br/>analyst"] --> A2["Architect"] --> A3["Code<br/>generator"] --> A4["DevOps<br/>engineer"] --> A5["Reviewer<br/>validate · repair · review"]
        end
        PIPE -- "LLMProvider (metered, budgeted)" --> P{{"anthropic | offline"}}
        PIPE -- "stages, files, events, usage" --> DB
        API -- "replay + follow events" --> DB
    end
    A5 -- "POST /v1/runs (files only, token)" --> R
    subgraph Runner["sandbox runner (the only Docker access)"]
        R["Job API<br/>fixed image · limits · runtime"]
    end
    R -- "docker run per test run" --> S["Sandbox container<br/>no network · read-only · non-root<br/>no caps · cpu/mem/pids · timeout"]
    DB[("PostgreSQL / SQLite")]
    P -. "HTTPS" .-> Claude["Anthropic API"]
    Prom["Prometheus"] -- "/metrics" --> API
    Service -. "OTLP (optional)" .-> OTel["Jaeger / OTel collector"]
```

How a job flows:

1. `POST /api/v1/jobs` stores the job as `queued` together with its five stage rows, and returns
   `202`.
2. A worker claims it with a compare-and-set `UPDATE … WHERE status = 'queued'`. Any replica may
   win, but only one does.
3. The orchestrator runs each agent. After every stage it commits the artifacts, the files and a
   `JobEvent` before notifying listeners, so the UI never shows progress that is not durably
   stored.
4. The reviewer validates every file. If there are errors it runs one repair round through the
   code generator. It then runs the project's lint, type check and tests in the sandbox and, while
   they fail and the repair budget lasts, sends the failures back to the code generator and runs
   them again. Finally it asks the model for a qualitative review that includes the test results.
5. The SSE endpoint replays `job_events` after the client's cursor and then follows new events.
   Events are polled from the database and woken early by an in-process notifier, which keeps
   this correct across replicas.

Every model call goes through a metering layer that checks the principal's budget reservation
first, prices the call from the pricing table afterwards, records it in the usage ledger, and
emits a tracing span. The sandbox runner is a separate process so that the internet-facing API,
which handles untrusted model output, never holds the root-equivalent Docker socket.

## Quickstart

### Docker (PostgreSQL + app)

```bash
docker compose up --build
# open http://localhost:8000   (set AUTOSWE_PORT=9000 to use another port)
```

The compose stack is a local demo. It disables auth and uses the offline provider unless you pass
`ANTHROPIC_API_KEY`:

```bash
ANTHROPIC_API_KEY=sk-ant-... docker compose up --build
```

It also builds the sandbox image and starts the sandbox runner, so every job's generated tests run
in a container (see the job's **tests** in `GET /api/v1/jobs/{id}`). The runner mounts the Docker
socket; on a Linux host set `DOCKER_SOCKET_GID=$(stat -c %g /var/run/docker.sock)` so it can use
it without running as root. To see traces, start the optional Jaeger service and point the
services at it:

```bash
OTEL_EXPORTER_OTLP_ENDPOINT=http://jaeger:4318 docker compose --profile tracing up --build
# traces: http://localhost:16686 (set JAEGER_UI_PORT to change), services autoswe and
# autoswe-sandbox-runner
```

### Local development

Requires Python 3.12+, [uv](https://docs.astral.sh/uv/) and Node 24.

```bash
uv sync                                  # Python deps from uv.lock
(cd frontend && npm ci && npm run build) # UI, served by FastAPI from frontend/dist
cp .env.example .env                     # AUTH_DISABLED=true, SQLite in ./data
uv run autoswe serve                     # http://localhost:8000, API docs at /api/docs
```

For UI work, run `npm run dev` in `frontend/`. It proxies `/api` to `localhost:8000`. The
product page is at `/` and the studio at `/app`.

### CLI

```bash
uv run autoswe generate "A REST API for a small library where members borrow books and librarians track loans with due dates." --out ./library-api
cd library-api && uv run ruff check . && uv run pytest   # uv creates the project's own .venv
```

Add `--sandbox docker` to run the generated tests (and the repair loop) in a sandbox container;
build its image once with `docker build -f sandbox/Dockerfile -t autoswe-sandbox:local .`.
`--stack typescript` or `--stack go` generates the other stacks; their sandbox images are
`sandbox/node/Dockerfile` (`autoswe-sandbox-node:local`) and `sandbox/go/Dockerfile`
(`autoswe-sandbox-go:local`).
`--sandbox local` uses a plain subprocess instead: no isolation, for development only.

## Using Claude

Set `ANTHROPIC_API_KEY`. With `LLM_DEFAULT_PROVIDER=auto` (the default), Claude becomes the default
provider, and the offline provider stays available per job (`"provider": "offline"`). Each agent
call works as follows:

- It is one streamed Messages API request (`client.beta.messages.stream`).
- The output is constrained by `output_config.format`, built from the agent's Pydantic artifact,
  and the answer is validated again with Pydantic.
- It uses adaptive thinking with a per-agent `effort` (override with `ANTHROPIC_EFFORT`).
- The static system prompt is marked cacheable.
- `fallbacks: "default"` is enabled, so a model refusal is retried server-side. Disable it with
  `ANTHROPIC_SERVER_FALLBACKS=false`.
- Refusals, truncation (`max_tokens`), an exhausted context window, schema mismatches, rate
  limits, overloads (including an `error` event in the middle of a stream) and auth errors surface
  as typed errors and fail the job with a clear message; transient ones say the job can be
  resubmitted.
- Token counts include cache reads and writes, and every attempt of a server-side fallback
  (`usage.iterations`).

What is tested, and what is not: `tests/test_anthropic_wire.py` runs the official `anthropic` SDK
against a mocked HTTP transport that answers with Server-Sent Events in the documented Messages
streaming shapes (thinking and text blocks, `fallback` blocks, `stop_details`, `usage.iterations`,
mid-stream `error` events). It checks the exact request the SDK sends (URL, beta header, body) and
how each response is handled; `tests/test_anthropic_provider.py` covers the provider's own logic
with a stand-in client. No test calls the live API, and the Claude path was **not** run against
it while building this repository, so whether the API accepts this request today, and whether its
responses still look like this, is unverified.

## Configuration

All settings are environment variables (a `.env` file is read if present).

| Variable | Default | Purpose |
| --- | --- | --- |
| `DATABASE_URL` | `sqlite+aiosqlite:///./data/autoswe.db` | SQLAlchemy async URL (`postgresql+asyncpg://…` in production) |
| `DB_AUTO_MIGRATE` | `true` | Run Alembic migrations on startup (advisory-locked on PostgreSQL) |
| `API_KEYS` | (empty) | Comma-separated `principal:key` pairs; keys must be at least 16 characters |
| `AUTH_DISABLED` | `false` | Local development only. Without keys **and** without this flag, the app refuses to start |
| `LLM_DEFAULT_PROVIDER` | `auto` | `auto`, `anthropic` or `offline` |
| `OFFLINE_DELAY_S` | `0` | Seconds the offline provider waits per agent call, so demos can watch live progress (0–10) |
| `ANTHROPIC_API_KEY` | (empty) | Enables the Claude provider |
| `ANTHROPIC_MODEL` | `claude-opus-5-5` | Model ID |
| `ANTHROPIC_EFFORT` | (per agent) | Force one effort level for every agent |
| `ANTHROPIC_MAX_TOKENS` | `64000` | Output cap per agent call |
| `ANTHROPIC_SERVER_FALLBACKS` | `true` | Server-side refusal fallbacks |
| `WORKER_ENABLED` / `WORKER_CONCURRENCY` | `true` / `2` | In-process worker and its parallelism |
| `WORKER_DRAIN_S` | `20` | On shutdown, how long running jobs may finish before the rest are requeued |
| `SERVE_API` | `true` | `false` on worker-only processes (`autoswe worker` sets it) |
| `MAX_ACTIVE_JOBS_PER_OWNER` | `3` | Queued and running jobs allowed per API key (`429` beyond) |
| `MAX_REPAIR_ROUNDS` | `1` | Repair rounds after failed validation (0–3) |
| `JOB_TRANSIENT_RETRIES` / `JOB_RETRY_DELAY_S` | `2` / `30` | Automatic retries after a transient provider error, resuming at the failed stage; first delay in seconds, doubling with ±20% jitter, capped at 10 minutes |
| `JOB_STALE_AFTER_S` | `300` | Running jobs without a heartbeat for this long are failed |
| `MAX_GENERATED_FILES` / `MAX_FILE_BYTES` / `MAX_TOTAL_BYTES` | `200` / `512000` / `5000000` | Output limits |
| `LOG_FORMAT` / `LOG_LEVEL` | `json` / `INFO` | `console` for human-friendly logs |
| `CORS_ORIGINS` | (empty) | Extra allowed origins (the UI is same-origin) |
| `FORWARDED_ALLOW_IPS` | `127.0.0.1` | Proxies whose `X-Forwarded-*` headers are trusted |
| `METRICS_ENABLED` | `true` | Expose `/metrics` |
| `SANDBOX_BACKEND` | `none` | Where generated tests run: `remote` (the sandbox runner; compose default), `docker` (this process starts containers: root-equivalent, CI and local work only), `local` (subprocess, **not isolated**), `none` |
| `SANDBOX_RUNNER_URL` / `SANDBOX_RUNNER_TOKEN` | (empty) | The runner's address and shared token (at least 16 characters); required for `remote` |
| `SANDBOX_IMAGE` | `autoswe-sandbox:local` | Image Python sandbox containers start from (never pulled at run time) |
| `SANDBOX_IMAGE_TYPESCRIPT` / `SANDBOX_IMAGE_GO` | `autoswe-sandbox-node:local` / `autoswe-sandbox-go:local` | The TypeScript and Go sandbox images |
| `SANDBOX_DOCKER_RUNTIME` | (empty) | e.g. `runsc` to run sandbox containers under gVisor |
| `SANDBOX_TIMEOUT_S` / `SANDBOX_MEMORY_MB` / `SANDBOX_CPUS` / `SANDBOX_PIDS_LIMIT` | `120` / `768` / `1` / `128` | Per-run limits, set by the operator only |
| `SANDBOX_MAX_REPAIR_ROUNDS` | `2` | Test-repair rounds after a failing sandbox run (0-5) |
| `SANDBOX_TYPECHECK` | `true` | Run mypy in the sandbox |
| `TOKEN_BUDGET_DEFAULT` | (empty = unlimited) | Tokens (input incl. cache + output) per principal per period |
| `TOKEN_BUDGETS` | (empty) | Per-principal budgets, e.g. `alice:2000000,ci:500000` |
| `TOKEN_BUDGET_PERIOD` | `month` | `month`, `day` (UTC) or `total` |
| `JOB_TOKEN_RESERVATION` | `200000` | Tokens reserved at submission and per top-up while a job runs |
| `PRICING_FILE` | built-in | A replacement for `src/autoswe/pricing.toml` (USD per million tokens) |
| `GITHUB_PUBLISH_TOKEN` | (empty = off) | Token for publishing: fine-grained with Administration + Repository creation, Contents, Workflows and Pull requests (write) |
| `GITHUB_PUBLISH_OWNER` | (empty) | Organisation to create repositories in; empty = the token's account |
| `GITHUB_API_URL` | `https://api.github.com` | GitHub API (GitHub Enterprise, or the test fake); https only except loopback |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | (empty) | Turns on OTLP/HTTP trace export; the other standard `OTEL_*` variables apply |

## API

Authenticate with `Authorization: Bearer <key>` or `X-API-Key: <key>`. Interactive docs are at
`/api/docs`.

| Method | Path | Description |
| --- | --- | --- |
| `POST` | `/api/v1/jobs` | Submit `{spec, title?, provider?, stack?}` (`stack`: `python`, `typescript` or `go`); returns `202` with the job, or `402` with `code: token_budget_exceeded` when the principal's budget cannot cover the reservation. An optional `Idempotency-Key` header makes retries safe: a repeat returns the same job with `Idempotent-Replayed: true`, and reusing the key with a different body is a `422` ([ADR 0010](docs/adr/0010-idempotent-job-submission.md)) |
| `GET` | `/api/v1/jobs` | Your jobs, newest first (`limit`; page with `before` and `before_id` from `next_cursor` and `next_cursor_id`) |
| `GET` | `/api/v1/jobs/{id}` | Status, per-stage progress, durations, tokens (cache included), cost, validation and test summaries, `trace_id`, `request_id`, `error_code` |
| `GET` | `/api/v1/jobs/{id}/events` | **SSE** stream; replays history, follows live, ends with `end` |
| `GET` | `/api/v1/jobs/{id}/requirements` | Requirements artifact |
| `GET` | `/api/v1/jobs/{id}/architecture` | Structured architecture spec |
| `GET` | `/api/v1/jobs/{id}/review` | Review report plus the full validation report |
| `GET` | `/api/v1/jobs/{id}/tests` | Sandboxed test run: final lint, type check and test results with failures, and every attempt before each repair round |
| `GET` | `/api/v1/jobs/{id}/files` | File tree metadata (path, size, producing stage, language) |
| `GET` | `/api/v1/jobs/{id}/files/{path}` | One file's content |
| `GET` | `/api/v1/jobs/{id}/archive` | The generated project as a `.zip` |
| `POST` | `/api/v1/jobs/{id}/cancel` | Cancel a queued or running job |
| `POST` | `/api/v1/jobs/{id}/retry` | Requeue a failed job; it resumes at the stage that failed. `202`, `402` (no budget room for its reservation), `409` (not failed), `429` (too many active jobs) |
| `DELETE` | `/api/v1/jobs/{id}` | Delete a finished job |
| `POST` | `/api/v1/jobs/{id}/publish` | `{repository, visibility?, description?}`: new GitHub repository plus a pull request; `201`, `409` (name taken or already published), `503` (not configured or rate-limited) |
| `GET` | `/api/v1/usage` | Your budget for the current period (limit, used, reserved, remaining, reset time) and token and cost totals |
| `GET` | `/api/v1/pricing` | The pricing table costs are computed from |
| `GET` | `/api/v1/info` | Public: version, whether auth is required, providers |
| `GET` | `/healthz`, `/readyz`, `/metrics` | Liveness, readiness (DB and worker), Prometheus |

```bash
curl -s -X POST localhost:8000/api/v1/jobs -H 'Content-Type: application/json' \
  -d '{"spec": "Track expenses with description, amount and spent_on date."}'
curl -N localhost:8000/api/v1/jobs/<id>/events
```

## Sandboxed tests and the repair loop

A run's outcome is one of `passed`, `failed`, `no_tests`, `timeout`, `resource_limit` (with
`limit`: `memory`, `pids`, `output` or `archive`) or `error` (the sandbox itself failed, which
is never blamed on the code). `failed`, `timeout` and `resource_limit` are repairable.

The sandbox's guarantees are tested against real containers in `tests/test_sandbox_docker.py`,
written from an attacker's side: each test tries an escape and asserts that it failed. On this
build, inside the sandbox: connecting to `1.1.1.1:443` and to the Docker host gateway fail with
`Network is unreachable`, DNS fails, writing to site-packages or over the entry script fails with
`Read-only file system`, `/var/run/docker.sock` does not exist, `setuid(0)` fails with
`Operation not permitted`, the effective capability set is `0000000000000000`, and no host
secret appears in the environment. A fork bomb ends as a `pids` limit, a 4 GB allocation as a
`memory` limit, an infinite loop as a `timeout`, a stdout flood and a disk fill hit the file-size
limit and the tmpfs size. `tests/test_sandbox_runner.py` attacks the runner's API (no or wrong
token, extra fields such as `image` or `command`, unsafe paths, oversized and streamed bodies,
queue exhaustion).

What the sandbox does not do: it protects the host, not the honesty of the verdict. A generated
`conftest.py` can mark its own tests as passed; structural validation and the review are separate
checks. The Kubernetes and ECS deployments in `deploy/` do not run the sandbox runner yet.

## Evals

```bash
docker build -f sandbox/Dockerfile -t autoswe-sandbox:local .
docker build -f sandbox/node/Dockerfile -t autoswe-sandbox-node:local .
docker build -f sandbox/go/Dockerfile -t autoswe-sandbox-go:local .
uv run autoswe eval run --sandbox docker          # replay, score, check evals/thresholds.yaml
uv run autoswe eval run --sandbox local           # without Docker (no isolation)
ANTHROPIC_API_KEY=sk-ant-... uv run autoswe eval run --live --sandbox docker
ANTHROPIC_API_KEY=sk-ant-... uv run autoswe eval record --source anthropic --sandbox docker
```

`eval run` writes `eval-report/eval-report.md` and `.json` and exits non-zero when a suite mean
falls below its threshold or a gated metric was not measured. The replay refuses any call whose
agent, prompt or output schema differs from the recording, so a prompt change without re-recording
fails CI instead of passing on stale answers. The recordings in `evals/recordings` were produced by
the deterministic offline provider (`autoswe eval record --source offline-provider`); their
`provenance` field says so, and none of them is Claude output. The `clinic` fixture is a known weak
spot of the offline heuristics (it reads "book" as an entity and misses appointments and vets);
the thresholds pin that measured baseline rather than hide it.

## Budgets and cost

Tokens are input (cache reads and writes included, as Anthropic bills them) plus output. At
submission a metered job reserves `JOB_TOKEN_RESERVATION` tokens with a single compare-and-set
update in the same transaction that creates it; while it runs it converts reservation into usage
call by call and tops up the same way, and it stops with `error_code: token_budget_exceeded` when
it cannot. Unused reservation is released when the job ends, however it ends. A call already in
flight finishes, so a principal can go over by at most one call. The offline provider bills
nothing and is never budgeted.

Costs come from `src/autoswe/pricing.toml`: USD per million tokens for input, output, cache reads
and 5-minute cache writes, taken from Anthropic's claude-api skill model table (prices cached
2026-10-06) and its prompt-caching multipliers. A model missing from the table is counted but not
priced, and its costs are reported as incomplete rather than estimated. `autoswe usage` prints
the ledger for every principal.

## Publishing to GitHub

```bash
GITHUB_PUBLISH_TOKEN=github_pat_... uv run autoswe serve
curl -X POST localhost:8000/api/v1/jobs/<id>/publish -H 'Content-Type: application/json'   -d '{"repository": "library-api", "visibility": "private"}'
```

The answer has the repository, the branch, the commit and the pull request URL, which the job
detail also shows under `publication`. To try it without touching github.com, run the fake API the
tests use and point the server at it:

```bash
uv run python -m tests.fake_github --port 5366 --token fake-token-0123456789 &
GITHUB_PUBLISH_TOKEN=fake-token-0123456789 GITHUB_API_URL=http://127.0.0.1:5366 uv run autoswe serve
```

## What a generated project contains

From the offline provider (Claude follows the same contract):

```text
library-api/
├── app/            main.py, db.py, models.py, schemas.py, routers/<entity>.py
├── migrations/     env.py, script.py.mako, versions/0001_initial.py   (+ alembic.ini)
├── tests/          conftest.py (migrated SQLite), test_<entity>.py, test_migrations.py, test_health.py
├── pyproject.toml  README.md  ARCHITECTURE.md (Mermaid + ADRs)  docs/architecture.json
├── Dockerfile      .dockerignore    (multi-stage, non-root, HEALTHCHECK)
├── .github/workflows/ci.yml          (ruff, pytest, image build)
└── deploy/
    ├── k8s/        deployment.yaml, service.yaml, kustomization.yaml
    └── terraform/  ECR + ECS Fargate (versions, variables, main, outputs)
```

The other stacks share `ARCHITECTURE.md`, `docs/`, `deploy/` and the CI and Docker files (for
their own toolchain):

```text
TypeScript: package.json  tsconfig.json  eslint.config.js
            src/   server.ts, app.ts (route table), models.ts, validation.ts, store.ts
            test/  <entity>.test.ts, health.test.ts (node:test + fetch on an ephemeral port)
Go:         go.mod  main.go (-healthcheck flag)  handlers.go (ServeMux patterns)
            models.go  validation.go  store.go  <entity>_test.go  helpers_test.go (httptest)
```

The test suite and CI prove this output works. They run the generated project's own `ruff` and
`pytest`, `terraform validate` its Terraform, and run `kubeconform` on its manifests. During
development its Docker image was also built and run, and it reported `healthy`.

## Testing and quality gates

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy   # lint, format, strict types
uv run pytest -q --cov --cov-report=term                              # 500 tests, coverage gate 95.5%
cd frontend && npm run lint && npm run typecheck && npm test && npm run build   # 43 unit tests
npm run e2e        # in frontend/, after the build: 34 Playwright + axe tests on the real stack
```

The backend count is what this branch collects: 497 pass and 3 skip on a Windows machine with
Docker, the sandbox images and a built frontend (coverage 96.5%). The skips are the two PostgreSQL
tests (the budget race, and the generated project's migrations) without
`AUTOSWE_TEST_POSTGRES_URL`, and a POSIX-only test. CI runs the PostgreSQL tests against a service
container and the real-container sandbox tests in a separate job.

The backend tests cover:

- Agent pipeline behaviour, including repair rounds, stage file ownership (case-insensitive), and
  rejection of unsafe paths produced by the LLM, including names unsafe only on Windows or macOS.
- Every validator.
- The offline heuristics.
- API behaviour and validation errors.
- Per-principal isolation and auth edge cases.
- SSE replay and resume, including malformed `Last-Event-ID` headers.
- Queue semantics: concurrent claim races, a cancel racing a claim, cancellation of a running job,
  crash containment, requeue on shutdown, stale-job reaping (a reaped job keeps its outcome), and
  two app replicas sharing one database.
- Metrics, security headers and the CSP, serving the UI (precompressed and immutable assets,
  the page for each route) and path traversal.
- That the migrations match the models.
- The Anthropic provider: its own logic (stand-in client) and the real SDK over a mocked HTTP
  transport (see [Using Claude](#using-claude)).
- An end-to-end test that runs a generated project's own lint and tests.
- The sandbox: the Docker backend's argument vector (the isolation contract), its control flow
  against a fake Docker CLI (hard timeout, OOM, output cap, cleanup), real containers including
  every escape attempt, the local backend, the entry script's archive checks, and the runner API.
- The test-and-repair loop: pass, fail then repair then pass, repair budget exhausted, timeouts
  and limits fed back, sandbox errors not blamed on the code, stage ownership during repair.
- The eval harness: route extraction, scoring, recording and replay (including stale recordings),
  the gate, the report and the CLI; the checked-in recordings must replay against current code.
- Budgets and cost: pricing arithmetic, fallback pricing, 402 at submission, a job stopping
  mid-run, reservation release on every terminal path, and concurrent submissions on SQLite and
  PostgreSQL that accept exactly as many jobs as fit.
- Tracing, with an in-memory exporter: the span tree from request to model call, trace continuation
  across the queue and to the runner, attributes, error statuses, retries and log correlation.
- That the product page's sample data still matches what the offline pipeline produces.

The Playwright suite starts the real server (FastAPI, worker, offline provider) on port 5150
and checks the product page and the studio. axe finds no serious or critical WCAG 2.2 AA issues
on the product page (both themes, desktop and phone), the new-project page, a finished job (both
themes), the command palette, the delete dialog and the not-found page. Nothing overflows at 390px
or 320px; a job runs live through all five agents; the product page is complete without
JavaScript; the scroll story scrubs and reverses, and reduced motion gets the complete static
story with nothing faded; count-ups land on the exact static values; a job card opens its page
through one view transition (none with reduced motion); and the carousel, keyboard focus, appearance (including blocked storage), command
palette, dialogs and not-found states work.
Set `E2E_SERVER_COMMAND` if `uv` is not on your `PATH`.

## Deploying the platform

**Kubernetes** (`deploy/k8s`, Kustomize). Includes:

- Namespace with the `restricted` Pod Security level.
- The API Deployment (`WORKER_ENABLED=false`, CPU-based HPA) and a separate `autoswe-worker`
  Deployment running `autoswe worker`, with a KEDA `ScaledObject` on queue depth (1-10 replicas,
  one per two queued or running jobs) and a `TriggerAuthentication` for its PostgreSQL scaler.
  KEDA must be installed in the cluster.
- A migration init container.
- Startup, liveness and readiness probes.
- Read-only root filesystem.
- HPA (2–6 replicas), PDB and NetworkPolicy.

Build and push the image, create the secret, then apply:

```bash
kubectl create namespace autoswe
kubectl -n autoswe create secret generic autoswe \
  --from-literal=DATABASE_URL='postgresql+asyncpg://user:pass@host:5432/autoswe' \
  --from-literal=API_KEYS="ci:$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"
cd deploy/k8s && kustomize edit set image autoswe=<registry>/autoswe:<tag> && kubectl apply -k .
```

Add `--from-literal=KEDA_POSTGRES_CONNECTION='postgresql://keda_ro:...@host:5432/autoswe'` to the
secret for the worker autoscaler (a role that can only read `jobs` is enough). To watch the
scaling on a local kind cluster, which the script creates and deletes:

```bash
docker build -t autoswe:ci . && bash deploy/kind/scale-demo.sh
```

On this build it scaled the workers from 1 to 4 (the overlay's cap) for 10 queued jobs and back to
1 afterwards, with every job succeeding, including 4 jobs that were running during a rolling
restart of all workers.

**AWS** (`deploy/terraform`). Provisions:

- ECR.
- ECS Fargate service behind an ALB (HTTPS when `certificate_arn` is set; 300 s idle timeout for
  SSE).
- RDS PostgreSQL 17 (encrypted, deletion-protected).
- Secrets Manager entries for `DATABASE_URL`, API keys and the Anthropic key.
- CloudWatch Logs.
- Least-privilege security groups.

It deploys into an existing VPC. Copy `example.tfvars` to `terraform.tfvars`, then run
`terraform init && terraform plan`. Configure a remote encrypted backend first, because the state
holds secrets.

Validation status: both directories pass `terraform validate` / `terraform fmt -check` and
`kubeconform -strict` in CI (KEDA's CRDs from the CRDs catalog). The Kubernetes base has run on a
local kind cluster (`deploy/kind`); neither has been applied to a production cluster or an AWS
account.

## Project structure

```text
src/autoswe/
├── app.py, __main__.py         # app factory + lifespan; CLI (serve | migrate | generate)
├── api/                        # jobs (REST + SSE + zip), system (health, metrics, info), deps
├── pipeline/
│   ├── agents.py               # the five agents + PipelineState
│   ├── artifacts.py            # typed artifacts = LLM output schemas
│   ├── orchestrator.py         # runs stages, persists progress, emits events
│   ├── validation.py           # parsers, policy checks, secret scan, path safety
│   └── prompts.py, docs.py, packaging.py, naming.py
├── stacks.py                   # target stacks: manifests, test files, sandbox profiles
├── sandbox/                    # backends (docker, local, remote), runner service, in-sandbox entry
├── evals/                      # eval harness: fixtures, recordings, routes, scoring, report, CLI
├── budget.py, metering.py, pricing.py, pricing.toml   # budgets, per-call metering, prices
├── tracing.py                  # OpenTelemetry setup (standard OTEL_* env) and helpers
├── llm/
│   ├── base.py                 # LLMProvider protocol, request/response, typed errors
│   ├── anthropic_provider.py   # Claude via the official SDK
│   ├── offline/                # deterministic analysis, blueprint, templates (Python, TS, Go)
│   └── registry.py
├── models.py, repository.py, db.py, migrations/   # SQLAlchemy 2.0 + Alembic
├── worker.py, events.py        # DB-backed queue (drains on shutdown), notifier
├── web.py                      # serves the UI: page per route, precompressed immutable assets
└── security.py, observability.py, logging_config.py, config.py, schemas.py
frontend/                       # Vite + React + TypeScript + Tailwind UI
├── boot.js                     # inline before first paint: theme, chunk preloads, early fetch
├── scripts/                    # build steps: prerender pages, compress assets, subset fonts
├── src/landing/                # product page (/), sample.json = one real offline run
├── src/app/                    # studio (/app): shell, timeline, activity, command palette
└── e2e/                        # Playwright + axe tests
sandbox/                        # sandbox images: Python (hash-locked), node/ (lockfile), go/ (seed cache)
evals/                          # eval fixtures, recordings, thresholds
docs/adr/                       # architecture decision records
deploy/k8s, deploy/terraform    # deploying the platform itself
tests/                          # pytest suite
```

## Design decisions and trade-offs

- **The database is the queue.** Adding a broker (Redis, SQS) would mean another moving part.
  Instead, a compare-and-set claim on the `jobs` table, plus heartbeats and a reaper, gives
  at-most-once execution across replicas with no extra infrastructure. The trade-off is that
  workers poll, which fits long-running LLM jobs but not very high-throughput queues; a broker
  would be the next step there.
- **One image, two roles.** Locally and in compose the worker runs in-process with the API, so
  there is one process to start. In Kubernetes the same image runs as an API Deployment
  (`WORKER_ENABLED=false`) and a worker Deployment (`autoswe worker`), so each scales on its own
  signal: CPU for the API, queue depth (KEDA) for the workers.
- **SSE over WebSockets.** Progress flows one way, and SSE gives automatic reconnection with
  `Last-Event-ID`, which maps directly onto the persisted event log. The UI uses `fetch` streaming,
  so the API key travels in a header rather than the URL.
- **Typed artifacts as the contract.** Agents exchange Pydantic models whose JSON Schema doubles
  as the structured-output schema. Providers are therefore swappable, and the offline provider can
  answer from structured context instead of parsing prose.
- **Deterministic validation before LLM review.** Parsers and rules catch the defects that matter
  (broken syntax, root containers, leaked keys, unsafe paths) cheaply and reproducibly. The LLM
  reviewer then adds judgement on top. Repair is bounded so a job cannot loop forever.
- **A separate process holds the Docker socket.** Mounting it into the API container would make
  any code execution in the internet-facing service root on the host. The sandbox runner accepts
  only files, decides image, command and limits itself, and is reachable only from the API
  ([ADR 0001](docs/adr/0001-sandboxed-test-execution.md)).
- **Budgets are reservations, not checks.** A check followed by spending lets concurrent jobs
  overspend; a compare-and-set reservation in the same transaction as the job insert cannot, on
  SQLite or PostgreSQL, and needs no lock ([ADR 0002](docs/adr/0002-token-budgets-and-cost.md)).
- **A narrow target stack.** Generated projects target Python 3.12, FastAPI and SQLAlchemy. A
  narrow target makes validation meaningful: CI runs the generated tests.
- **Honest offline mode.** The offline provider is a transparent heuristic, not a fake LLM. It
  reports zero tokens, and its review says it does not judge business logic.

## Limitations

- The offline provider only understands CRUD-shaped specs: entities, fields and many-to-one
  links, with no business rules. Many-to-many links have to be written as a join entity, and
  self-references (an employee's manager) are not generated.
- Generated tests run in the sandbox only when one is configured (`SANDBOX_BACKEND`; compose sets
  it). The Kubernetes and AWS deployments in `deploy/` do not include the sandbox runner yet, and
  the gVisor runtime option has not been run.
- The eval recordings in the repository come from the offline provider. The harness, `--live` mode
  and the recording tool work with Claude, but no live recording has been made yet.
- A job that resumes reruns the interrupted stage from its start: there is no checkpoint inside
  a stage (e.g. between the coder's repair rounds).

## Roadmap (future work, not implemented)

- Many-to-many links as a first-class relation, and a per-link choice between CASCADE and
  RESTRICT (today every link cascades, and the review says so).
- A database behind the TypeScript and Go stores.

## License

[MIT](LICENSE)
