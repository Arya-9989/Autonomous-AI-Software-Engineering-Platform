# ADR 0007: TypeScript/Node and Go target stacks

- Status: accepted
- Date: 2026-10-23

## Context

Generated projects were Python only. The pipeline's value is the contract around the model:
typed artifacts, stage ownership, structural validation, and since ADR 0001 a sandbox that runs
the project's own checks and feeds failures back. A new stack is only worth adding if it gets that
whole contract, including lint, type check and tests run offline in the sandbox.

## Options considered

- **Frameworks** (Express or Fastify; gin or chi): familiar, but every dependency must be baked
  into a network-less sandbox image and kept in step with what the model is told it may import.
- **Standard library only (chosen)**: Node 24's `node:http`, `node:test` and native type
  stripping, and Go's `net/http` (method and path patterns since 1.22), `encoding/json` and
  `httptest`. Generated services have no runtime dependencies; the TypeScript stack needs only its
  type checker and linter, and the Go stack nothing at all.
- **A database per stack**: there is no standard-library SQL driver in Go, and Node's `node:sqlite`
  is still a release candidate in Node 24. The new stacks keep data in memory behind a store
  module, say so in the architecture (`database: memory`, one replica) and in the review, and leave
  the store as the seam where a database goes.

## Decision

- A job, the CLI and eval fixtures take `stack`: `python` (default, unchanged output), `typescript`
  or `go` (migration 0006). Every agent gets a fixed system prompt per (role, stack), so each stays
  a cacheable prefix; the Python prompts are byte-identical to before.
- The offline provider renders complete projects for both stacks (CRUD per entity, validation,
  `/healthz`, tests over real HTTP, Dockerfile, CI, Kubernetes and Terraform). The Go output is
  gofmt-clean: struct and literal columns are aligned the way gofmt aligns them.
- Structural validation knows each stack's manifest (`package.json`, `go.mod`), test files and
  source suffix (for design-to-code traceability).
- One sandbox image per stack, all on the same Python base so the entry script is identical:
  `sandbox/node` adds the Node 24 binary and the lockfile-pinned TypeScript 6.0.3, ESLint 10.13.0,
  typescript-eslint 8.72.0 and @types/node, linked into the workspace as `node_modules`;
  `sandbox/go` adds the Go 1.27.3 toolchain and a build cache seeded at image build (112 MiB),
  copied into the run's `/tmp`, so the standard library is not recompiled per run. Checks:
  ESLint (JSON), `tsc --noEmit`, `node --test` (JUnit); `gofmt -l`, `go vet`, `go test -json`.
  Failures come back in the same structured form as Python's and drive the same repair loop.
- The caller still picks only a profile name; the runner maps it to its own image
  (`SANDBOX_IMAGE_TYPESCRIPT`, `SANDBOX_IMAGE_GO`).
- Go runs get two differences from the other sandboxes, both deliberate: `/tmp` is mounted
  `exec`, because `go test` executes the test binary it compiles there (that binary is the
  generated code the sandbox exists to contain; the workspace stays `noexec`), and a 1 GiB memory
  and 384 MiB `/tmp` floor for the build cache. `GOPROXY=off` and `GOTOOLCHAIN=local` keep it
  offline.
- Static route extraction for the eval reads TypeScript route tables and Express-style calls and
  Go `ServeMux` patterns; each stack has two eval fixtures.

## Consequences

- The model is told exactly what exists in each sandbox; asking for a framework would fail the
  sandbox run, which is reported and fed back like any other failure.
- Images grow by about 400 MB (Node) and 700 MB (Go); CI builds all three.
- Verified by `tests/test_stacks.py` and the real-container cases in
  `tests/test_sandbox_docker.py`: generated projects pass lint, type check and 13 tests in their
  sandboxes; lint, type and test failures and Go build errors come back structured; and Go escape
  attempts (network, writes to the toolchain, `su`) fail.
