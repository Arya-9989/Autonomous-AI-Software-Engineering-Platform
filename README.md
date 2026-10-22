# Autonomous AI Software Engineering Platform

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/hero-dark.svg">
  <source media="(prefers-color-scheme: light)" srcset="docs/hero-light.svg">
  <img alt="AutoSWE: plain English in, deployable project out. The five-agent pipeline, all stages done; offline run on the library spec: 32 files, 0 errors, review 100/100." src="docs/hero-dark.svg">
</picture>

[![CI](https://github.com/Arya-9989/Autonomous-AI-Software-Engineering-Platform/actions/workflows/ci.yml/badge.svg)](https://github.com/Arya-9989/Autonomous-AI-Software-Engineering-Platform/actions/workflows/ci.yml)
![Python 3.12](https://img.shields.io/badge/python-3.12-3776ab)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

**AutoSWE** turns a natural-language specification into a reviewed, deployable repository.
A pipeline of five agents analyses requirements, designs the architecture, writes a FastAPI
service with tests, adds a Dockerfile, a GitHub Actions workflow, Kubernetes manifests and
Terraform, and then validates and reviews the result. Every file the agents produce is treated as
untrusted input: it is parsed with real parsers and checked against security and operability rules
before you can download it. Jobs run asynchronously with persisted, stage-by-stage progress that
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
- **Async jobs with durable state**: SQLAlchemy 2.0 (SQLite by default, PostgreSQL in compose and
  production), Alembic migrations, and a DB-backed queue with atomic claims. Heartbeats, a
  stale-job reaper, cancellation, and graceful requeue on shutdown make it safe to run several
  replicas.
- **Live progress over SSE**: the persisted event log is replayed, then followed live. Clients resume
  with `Last-Event-ID`.
- **Outputs**: a downloadable zip (deterministic archive), a file browser, `ARCHITECTURE.md` with a
  Mermaid diagram and ADRs, and machine-readable `docs/architecture.json`.
- **Observability**: Prometheus `/metrics` (HTTP latency by route template, jobs, stage durations,
  LLM tokens, validation issues), structured JSON logs carrying `request_id` and `job_id`, and
  `/healthz` and `/readyz` probes.
- **Security**: API-key auth with jobs scoped to each principal (cross-tenant access returns 404),
  per-principal active-job limits, a strict CSP (the UI's inline boot script is admitted by its
  hash, nothing else inline), security headers, a non-root container, and no secrets in code.
- **Web UI** (React, TypeScript, Tailwind, Motion, Radix):
  - A product page at `/`, dark-first, built from a real offline run of the pipeline: a replay
    of the run on the five-agent rail, a scroll story that shows what each agent produced, and an
    unedited view of the generated code. A test regenerates that data and fails on drift.
  - The studio at `/app`: spec composer with examples, a live pipeline timeline and streaming
    activity log, file tree with Shiki highlighting, architecture (Mermaid), review and
    requirements tabs, a ⌘K / Ctrl+K command palette, toasts and accessible dialogs.
  - Light and dark themes that follow the OS (with a manual switch), reduced-motion support,
    designed loading, empty and error states, and layouts from 320px up.
  - Fast on a phone: the product page and the studio's loading states are prerendered at build
    time and hydrated, assets are precompressed (Brotli, gzip) and cached as immutable, the
    interface fonts are subset, code is highlighted in a Web Worker, and everything not needed
    for the first paint loads on demand.
- **Deployment for the platform itself**: a Kustomize base and AWS Terraform (ECS Fargate, ALB,
  RDS, Secrets Manager).
- **CLI**: `autoswe generate "<spec>" --out ./project` runs the whole pipeline locally.

<table>
  <tr>
    <td><img src="docs/screenshots/landing-hero-dark.png" alt="Product page hero: Plain English in. Deployable project out."></td>
    <td><img src="docs/screenshots/landing-story-dark.png" alt="How it works: the architect's real output beside the pinned five-agent rail"></td>
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
        PIPE -- "LLMProvider" --> P{{"anthropic | offline"}}
        PIPE -- "stages, files, events" --> DB
        API -- "replay + follow events" --> DB
    end
    DB[("PostgreSQL / SQLite")]
    P -. "HTTPS" .-> Claude["Anthropic API"]
    Prom["Prometheus"] -- "/metrics" --> API
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
   code generator, then asks the model for a qualitative review.
5. The SSE endpoint replays `job_events` after the client's cursor and then follows new events.
   Events are polled from the database and woken early by an in-process notifier, which keeps
   this correct across replicas.

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
| `MAX_ACTIVE_JOBS_PER_OWNER` | `3` | Queued and running jobs allowed per API key (`429` beyond) |
| `MAX_REPAIR_ROUNDS` | `1` | Repair rounds after failed validation (0–3) |
| `JOB_STALE_AFTER_S` | `300` | Running jobs without a heartbeat for this long are failed |
| `MAX_GENERATED_FILES` / `MAX_FILE_BYTES` / `MAX_TOTAL_BYTES` | `200` / `512000` / `5000000` | Output limits |
| `LOG_FORMAT` / `LOG_LEVEL` | `json` / `INFO` | `console` for human-friendly logs |
| `CORS_ORIGINS` | (empty) | Extra allowed origins (the UI is same-origin) |
| `FORWARDED_ALLOW_IPS` | `127.0.0.1` | Proxies whose `X-Forwarded-*` headers are trusted |
| `METRICS_ENABLED` | `true` | Expose `/metrics` |

## API

Authenticate with `Authorization: Bearer <key>` or `X-API-Key: <key>`. Interactive docs are at
`/api/docs`.

| Method | Path | Description |
| --- | --- | --- |
| `POST` | `/api/v1/jobs` | Submit `{spec, title?, provider?}`; returns `202` with the job |
| `GET` | `/api/v1/jobs` | Your jobs, newest first (`limit`; page with `before` and `before_id` from `next_cursor` and `next_cursor_id`) |
| `GET` | `/api/v1/jobs/{id}` | Status, per-stage progress, durations, tokens, validation summary |
| `GET` | `/api/v1/jobs/{id}/events` | **SSE** stream; replays history, follows live, ends with `end` |
| `GET` | `/api/v1/jobs/{id}/requirements` | Requirements artifact |
| `GET` | `/api/v1/jobs/{id}/architecture` | Structured architecture spec |
| `GET` | `/api/v1/jobs/{id}/review` | Review report plus the full validation report |
| `GET` | `/api/v1/jobs/{id}/files` | File tree metadata (path, size, producing stage, language) |
| `GET` | `/api/v1/jobs/{id}/files/{path}` | One file's content |
| `GET` | `/api/v1/jobs/{id}/archive` | The generated project as a `.zip` |
| `POST` | `/api/v1/jobs/{id}/cancel` | Cancel a queued or running job |
| `DELETE` | `/api/v1/jobs/{id}` | Delete a finished job |
| `GET` | `/api/v1/info` | Public: version, whether auth is required, providers |
| `GET` | `/healthz`, `/readyz`, `/metrics` | Liveness, readiness (DB and worker), Prometheus |

```bash
curl -s -X POST localhost:8000/api/v1/jobs -H 'Content-Type: application/json' \
  -d '{"spec": "Track expenses with description, amount and spent_on date."}'
curl -N localhost:8000/api/v1/jobs/<id>/events
```

## What a generated project contains

From the offline provider (Claude follows the same contract):

```text
library-api/
├── app/            main.py, db.py, models.py, schemas.py, routers/<entity>.py
├── tests/          conftest.py (isolated SQLite), test_<entity>.py, test_health.py
├── pyproject.toml  README.md  ARCHITECTURE.md (Mermaid + ADRs)  docs/architecture.json
├── Dockerfile      .dockerignore    (multi-stage, non-root, HEALTHCHECK)
├── .github/workflows/ci.yml          (ruff, pytest, image build)
└── deploy/
    ├── k8s/        deployment.yaml, service.yaml, kustomization.yaml
    └── terraform/  ECR + ECS Fargate (versions, variables, main, outputs)
```

The test suite and CI prove this output works. They run the generated project's own `ruff` and
`pytest`, `terraform validate` its Terraform, and run `kubeconform` on its manifests. During
development its Docker image was also built and run, and it reported `healthy`.

## Testing and quality gates

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy   # lint, format, strict types
uv run pytest -q                                                      # 231 tests
cd frontend && npm run lint && npm run typecheck && npm test && npm run build   # 27 unit tests
npm run e2e        # in frontend/, after the build: 24 Playwright + axe tests on the real stack
```

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
- That the product page's sample data still matches what the offline pipeline produces.

The Playwright suite starts the real server (FastAPI, worker, offline provider) on port 5150
and checks the product page and the studio. axe finds no serious or critical WCAG 2.2 AA issues
on the product page (both themes, desktop and phone), the new-project page, a finished job (both
themes), the command palette, the delete dialog and the not-found page. Nothing overflows at 390px
or 320px; a job runs live through all five agents; the product page is complete without
JavaScript; and the carousel, keyboard focus, appearance (including blocked storage), command
palette, dialogs and not-found states work.
Set `E2E_SERVER_COMMAND` if `uv` is not on your `PATH`.

## Deploying the platform

**Kubernetes** (`deploy/k8s`, Kustomize). Includes:

- Namespace with the `restricted` Pod Security level.
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
`kubeconform -strict` in CI. They have **not** been applied to a real cluster or AWS account.

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
├── llm/
│   ├── base.py                 # LLMProvider protocol, request/response, typed errors
│   ├── anthropic_provider.py   # Claude via the official SDK
│   ├── offline/                # deterministic analysis, blueprint and templates
│   └── registry.py
├── models.py, repository.py, db.py, migrations/   # SQLAlchemy 2.0 + Alembic
├── worker.py, events.py        # DB-backed queue, notifier
├── web.py                      # serves the UI: page per route, precompressed immutable assets
└── security.py, observability.py, logging_config.py, config.py, schemas.py
frontend/                       # Vite + React + TypeScript + Tailwind UI
├── boot.js                     # inline before first paint: theme, chunk preloads, early fetch
├── scripts/                    # build steps: prerender pages, compress assets, subset fonts
├── src/landing/                # product page (/), sample.json = one real offline run
├── src/app/                    # studio (/app): shell, timeline, activity, command palette
└── e2e/                        # Playwright + axe tests
deploy/k8s, deploy/terraform    # deploying the platform itself
tests/                          # pytest suite
```

## Design decisions and trade-offs

- **The database is the queue.** Adding a broker (Redis, SQS) would mean another moving part.
  Instead, a compare-and-set claim on the `jobs` table, plus heartbeats and a reaper, gives
  at-most-once execution across replicas with no extra infrastructure. The trade-off is that
  workers poll, which fits long-running LLM jobs but not very high-throughput queues; a broker
  would be the next step there.
- **The worker runs in-process with the API.** This keeps deployment to one image and one
  Deployment. Splitting them later only needs `WORKER_ENABLED=false` on API pods and a worker
  Deployment.
- **SSE over WebSockets.** Progress flows one way, and SSE gives automatic reconnection with
  `Last-Event-ID`, which maps directly onto the persisted event log. The UI uses `fetch` streaming,
  so the API key travels in a header rather than the URL.
- **Typed artifacts as the contract.** Agents exchange Pydantic models whose JSON Schema doubles
  as the structured-output schema. Providers are therefore swappable, and the offline provider can
  answer from structured context instead of parsing prose.
- **Deterministic validation before LLM review.** Parsers and rules catch the defects that matter
  (broken syntax, root containers, leaked keys, unsafe paths) cheaply and reproducibly. The LLM
  reviewer then adds judgement on top. Repair is bounded so a job cannot loop forever.
- **A narrow target stack.** Generated projects target Python 3.12, FastAPI and SQLAlchemy. A
  narrow target makes validation meaningful: CI runs the generated tests.
- **Honest offline mode.** The offline provider is a transparent heuristic, not a fake LLM. It
  reports zero tokens, and its review says it does not judge business logic.

## Limitations

- The offline provider only understands CRUD-shaped specs: entities and fields, with no
  relationships between entities and no business rules.
- Generated code is validated structurally. The Claude path does not yet run the generated tests
  in a sandbox before review; the roadmap covers this.
- Agent calls are not retried at job level. A transient provider error fails the job, which can
  then be resubmitted.

## Roadmap (future work, not implemented)

- Run generated test suites in an isolated sandbox (container per job) and feed failures into
  repair.
- Relationships between entities and Alembic migrations in generated projects.
- More target stacks (TypeScript/Node, Go) with matching validators.
- Push generated projects to a new GitHub repository and open a PR.
- A separate worker Deployment with KEDA autoscaling on queue depth; an OpenTelemetry tracing
  exporter.
- Per-principal token budgets and cost reporting.

## License

[MIT](LICENSE)
