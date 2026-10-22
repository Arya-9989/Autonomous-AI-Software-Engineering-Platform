# ADR 0006: A separate worker Deployment scaled by KEDA on queue depth

- Status: accepted
- Date: 2026-10-23

## Context

The API and the queue worker ran in one Deployment, scaled by CPU. Jobs are long, mostly waiting
on model calls, so CPU says little about how much work is queued: a backlog can build up while CPU
is idle, and scaling the API for the workers' sake scales SSE handling too.

## Options considered

1. **CPU-based HPA on the combined Deployment** (the status quo): wrong signal.
2. **HPA on a Prometheus metric** (`autoswe_jobs_queued`) through prometheus-adapter: needs a
   Prometheus server and an adapter in the cluster, and the gauge is per process, last observed.
3. **KEDA with its PostgreSQL scaler (chosen)**: the queue *is* the `jobs` table, so the
   authoritative depth is one `SELECT COUNT(*)`. No extra moving parts beyond KEDA.

## Decision

- `autoswe worker` runs only the queue worker and serves `/healthz`, `/readyz` and `/metrics`
  (`SERVE_API=false`); the API Deployment runs with `WORKER_ENABLED=false`. Workers have no
  Service, their own NetworkPolicy, and the migration init container (advisory-locked).
- A `ScaledObject` targets the worker Deployment: query
  `SELECT COUNT(*) FROM jobs WHERE status IN ('queued', 'running')`, target 2 per replica
  (= `WORKER_CONCURRENCY`), 1 to 10 replicas, scale-down limited to one pod per minute after a
  two-minute stabilisation window. Running jobs are counted so busy workers are not scaled away
  while there is work. The connection comes from the `autoswe` Secret through a
  `TriggerAuthentication` (`KEDA_POSTGRES_CONNECTION`), ideally a role that can only read `jobs`.
- Graceful shutdown: on SIGTERM a worker stops claiming, lets running jobs finish for
  `WORKER_DRAIN_S` (40 s in the manifest, inside a 60 s grace period), then cancels the rest,
  which the orchestrator requeues for another worker. A hard kill is covered by the existing
  stale-job reaper. No path loses a job.
- CI validates the manifests with kubeconform, with KEDA's CRD schemas from the CRDs catalog.
  `deploy/kind/scale-demo.sh` proves the behaviour on a local kind cluster and deletes it.

## Consequences

- KEDA becomes a cluster prerequisite for the worker scaling (the manifests still apply without
  it, minus the ScaledObject).
- Minimum one worker: a first job does not wait for a cold start. Scale-to-zero is one setting
  away (`minReplicaCount: 0`) for clusters that prefer cost over latency.
- A requeued job restarts from its first stage; its spent tokens stay on its record (ADR 0002).
