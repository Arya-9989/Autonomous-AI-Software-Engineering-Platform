# ADR 0001: Sandboxed execution of generated test suites

- Status: accepted
- Date: 2026-10-23

## Context

The pipeline produces a Python project whose code was written by a model. Until now the platform
only checked it structurally (parsers, policy rules) and never ran it. Running the generated
tests is the strongest signal we can get, and their failures are the most useful input a repair
round can receive. But running them means **executing untrusted code**: a model can write a
test that opens sockets, reads the host's files, forks without end, allocates all memory, loops
forever or floods stdout, whether by mistake or because a spec was written to make it do so.

Requirements:

1. Each run is isolated from the host, from other runs and from the platform's secrets.
2. No network by default; CPU, memory and pid limits; a wall-clock timeout enforced from outside.
3. Read-only root file system with a writable, size-capped workspace; non-root user; no
   capabilities; no privilege escalation; nothing from the host mounted except the job's files.
4. Results come back as structured data (per-test failures, lint, type check) that a repair
   prompt can use, and limit violations are reported as outcomes, not crashes.
5. It works in `docker compose`, on a developer machine (including Windows with Docker
   Desktop), and in CI.

### The Docker-socket question

The obvious implementation mounts `/var/run/docker.sock` into the API container and starts a
container per run. Access to the Docker API is root-equivalent on the host: whoever can create
containers can create one with `--privileged` and the host's `/` mounted. The API container is
internet-facing, parses untrusted HTTP and LLM output, and holds the Anthropic key and database
credentials. Handing it the socket would turn any remote code execution in the API into host
root.

## Options considered

| Option | Isolation of the code under test | What a compromised API gets | Runs in compose / on Docker Desktop | Notes |
| --- | --- | --- | --- | --- |
| A. Socket in the API container | container | **host root** | yes | rejected |
| B. Socket proxy (e.g. a filtering proxy) in front of the API | container | still `POST /containers/create`, which accepts `Privileged`, `Binds`, `CapAdd` unless the proxy parses bodies | yes | filtering by endpoint does not filter the dangerous part |
| C. Separate **sandbox runner** service, the only holder of the socket, with a narrow job API | container (gVisor optional) | "run these files with the operator's fixed limits" | yes | **chosen** |
| D. Docker-in-Docker sidecar | container | root in a privileged DinD container (DinD needs `--privileged`) | yes | moves the problem, adds a privileged container |
| E. Kubernetes Job per run with a gVisor or Kata `RuntimeClass` | VM-grade | create Jobs in one namespace (RBAC) | no (needs a cluster) | the right shape for the K8s deployment; follow-up |
| F. Firecracker microVMs | VM | depends on the controller | no (needs KVM; not on Docker Desktop) | strongest, heaviest |
| G. Plain subprocess (`resource` limits, `unshare`) | none to weak | n/a | partly (no namespaces on Windows) | only as a labelled dev fallback |

## Decision

**C, with G as an explicitly unsafe development fallback, and gVisor as an opt-in runtime.**

- `autoswe sandbox-runner` (`src/autoswe/sandbox/service.py`) is a separate process and image
  (`Dockerfile --target sandbox-runner`). It is the only component with the Docker socket. In
  compose it sits on an `internal: true` network shared only with `app`, publishes no port, runs
  as uid 10001 with only the socket's group added, a read-only root file system, all
  capabilities dropped and `no-new-privileges`.
- Its API is `POST /v1/runs` with `{files: [{path, content}]}` and nothing else (`extra` fields
  are rejected). The **image, command, limits and runtime come from the runner's own
  configuration**; the caller cannot choose them. Requests carry a shared bearer token compared
  in constant time; bodies are capped while they stream; paths go through the pipeline's
  path-safety rules; concurrent and queued runs are bounded (`503` with `Retry-After` beyond).
  A compromised API can therefore only ask for more sandbox runs with the operator's limits.
- Each run is `docker run` with: `--network none`, `--read-only`, tmpfs `/workspace` and `/tmp`
  (`nosuid,nodev,noexec`, size-capped), `--user 10002:10002`, `--cap-drop ALL`,
  `--security-opt no-new-privileges:true`, `--memory` = `--memory-swap` (no swap), `--cpus`,
  `--pids-limit`, `--ulimit nofile/fsize`, `--init`, `--log-driver none`, `--pull=never`, and
  `--runtime runsc` when `SANDBOX_DOCKER_RUNTIME=runsc` (gVisor). The argument vector is built in
  one function and pinned by a unit test.
- **Nothing is mounted.** The workspace is streamed into the container as a tar on stdin and
  unpacked by the in-sandbox entry script with `tarfile`'s `data` filter (no absolute paths,
  `..`, links out of the tree, devices or setuid bits). This also avoids the Docker-out-of-Docker
  bind-mount path problem: the runner's file system never has to be visible to the daemon.
- The runner enforces the wall-clock limit from outside (`docker kill`, then `docker rm -f`), reads
  `State.OOMKilled`, caps the bytes it reads from the container, and removes leftover containers
  of its pool when it starts. Inside, the entry script gives ruff, pytest and mypy one shared
  deadline, runs each in its own process group with a scrubbed environment, keeps only the tail
  of their output, and reads the container's cgroup counters (`memory.events` `oom_kill`,
  `pids.events` `max`) to tell a memory or pid limit from an ordinary failure.
- The sandbox image (`sandbox/Dockerfile`) is the fixed target environment: Python 3.12 and a
  hash-locked set of packages (`sandbox/requirements.txt`). The prompts tell the model exactly
  which packages exist, because nothing can be installed at run time.
- `SANDBOX_BACKEND` selects `remote` (the runner; compose default), `docker` (this process
  starts containers: only for CI and local work, and it logs a warning saying it is
  root-equivalent), `local` (a subprocess with a scrubbed environment, a wall-clock limit and,
  on POSIX, address-space and CPU rlimits; **its results say `isolation: NONE`**), or `none`
  (the default for a bare `autoswe serve`, which keeps the previous behaviour).

## Consequences

- The Docker socket is still root-equivalent; we have contained *who* holds it, not removed it.
  On a Linux host the runner can use rootless Docker or Podman (any Docker-compatible CLI via
  `SANDBOX_DOCKER_BINARY`), and gVisor (`runsc`) adds a user-space kernel between the generated
  code and the host kernel. Docker Desktop on the development machine has no `runsc`, so the
  gVisor path is configuration-only and **was not run** while building this.
- Docker's default seccomp and AppArmor profiles stay on. A kernel exploit from inside a runc
  container would still reach the host kernel; gVisor or a microVM (options E and F) is the
  answer for hostile multi-tenant traffic.
- A generated suite can lie about its own result (a `conftest.py` can mark every test passed, or
  write to the report file). The sandbox protects the host, not the verdict's integrity against
  adversarial code; the structural validators and the reviewer remain separate checks.
- Runs cost a container start (about 1 s on Docker Desktop) plus the checks: about 7-8 s for
  the library project with type checking.
- The Kubernetes and ECS deployments in `deploy/` do not include the runner yet; there the
  sandbox stays `none` until option E is built.
- The local backend's results are clearly labelled; the eval harness and tests use it where
  Docker is unavailable, never to claim isolation.

## Verification

`tests/test_sandbox_docker.py` runs real containers and asserts that every escape attempt in
`tests/sandbox_projects.py` fails (internet, DNS, the Docker host gateway, writes to
site-packages, the entry script and `/etc`, the Docker socket, `setuid(0)`, effective
capabilities, host secrets in the environment), and that a fork bomb ends as a `pids` limit, a
memory hog as a `memory` limit, an infinite loop as a `timeout`, and that output floods and disk
fills are contained. `tests/test_sandbox_runner.py` attacks the runner API (no or wrong token,
extra fields such as `image` and `command`, unsafe paths, oversized and streamed bodies, queue
exhaustion).
