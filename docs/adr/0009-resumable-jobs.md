# ADR 0009: Resumable jobs after transient provider failures

- Status: accepted
- Date: 2026-10-23

## Context

A job is five model-backed stages. The Anthropic SDK already retries a single HTTP request
(`ANTHROPIC_MAX_RETRIES`), but once those retries run out, the stage fails and the whole job
fails with it. A rate limit or an overloaded API in the reviewer threw away analysis,
architecture, code and delivery files that had already been generated and paid for, and the
only way forward was to submit again and pay for all five stages. `LLMTransientError.retryable`
was declared for this purpose but nothing read it. A worker restart had the same flaw: it
requeued a job with a clean slate.

## Options considered

- **Retry inside the stage, in-process** (sleep, call again): it holds a worker slot and the
  job's heartbeat through the wait, and a restart in the meantime still loses the work.
- **Requeue the whole job**: simple, but it pays again for every stage that finished.
- **Requeue with checkpoints (chosen)**: each stage's results are already persisted when it
  succeeds (requirements and architecture on the job row, files with their producing stage,
  stage status and usage). On a transient failure the job goes back to `queued` with a
  `run_after` time. The next attempt restores the finished stages from those rows and starts at
  the first stage that did not finish. The wait holds no worker slot, and a restart during it
  loses nothing.

## Decision

- **Automatic retries.** When a stage fails with an error whose `retryable` is true, and the job
  has used fewer than `JOB_TRANSIENT_RETRIES` retries (default 2), the orchestrator:
  - emits `stage.failed`;
  - and `JobRepository.schedule_retry` then:
    - moves the job `running` → `queued` (compare-and-set on the status, so a reaped or finished
      job is left alone);
    - sets `run_after = now + delay`, where the delay is `JOB_RETRY_DELAY_S` (default 30 s),
      doubled per retry, ±20% jitter, capped at 10 minutes;
    - increments `retries` and records `job.retry_scheduled` with the stage, attempt, delay and
      error.

  The job keeps its budget reservation while it waits. `claim_next` skips queued jobs whose
  `run_after` is in the future. Non-retryable errors (refusals, invalid output, configuration,
  budget exhaustion) fail at once, as before.
- **Resume.** On claim, `Orchestrator._restore` walks the stages in order and stops at the first
  one that did not succeed, or whose stored artifact no longer validates (a schema change
  between versions). It loads the requirements, architecture and files of the stages before
  that point into the pipeline state, discards files from any later stage, and records
  `job.resumed`. Then only the remaining stages run. The token and cost totals continue from the
  earlier attempts, which were billed.
- **Manual retry.** `POST /api/v1/jobs/{id}/retry` requeues a `failed` job in the same way. It
  answers 409 if the job is not failed and 429 at the active-job limit. The reservation was
  released when the job failed, so a new one is taken in the same transaction as the requeue;
  if it no longer fits, the answer is 402 and the job is unchanged. The automatic retry count
  starts again from zero.
- **Shutdown requeue** (`reset_for_retry`) uses the same checkpoint-preserving requeue: a rolling
  deploy no longer re-bills the stages that finished.
- **Observability.** `autoswe_job_retries_total{reason="transient"|"manual"}`. A requeued run is
  not counted in `autoswe_jobs_finished_total`. Job spans carry
  `autoswe.job.resumed_stages`, and stage spans `autoswe.stage.retry_scheduled`. The job
  detail adds `retries`, and `run_after` while the job waits. The UI's activity log describes
  the three new events.

## Consequences

- Tests cover:
  - a transient architect failure: the analyst ran once, the architect twice, and the job
    succeeded with `retries: 1`;
  - the retry bound;
  - non-retryable errors;
  - a manual retry after a DevOps failure: only DevOps ran again;
  - the `run_after` gate on claims;
  - the 402 path, where only the reviewer ran again after budget room was restored;
  - a checkpoint that no longer validates, which is rerun;
  - the shutdown requeue keeping the analyst's and architect's work.
- The interrupted stage itself starts over. There is no checkpoint inside a stage, so a
  coder that failed after a repair round reruns the round.
- A `queued` job may have run before: its stages, `retries` and event log show the earlier
  attempts. Clients that took `queued` to mean "never ran" should look at those.
- A provider error that is retryable but persistent (an account-level rate limit) costs up to
  `JOB_TRANSIENT_RETRIES` extra attempts of one stage before the job fails, and the wait between
  them grows to about 2 minutes with the defaults.
