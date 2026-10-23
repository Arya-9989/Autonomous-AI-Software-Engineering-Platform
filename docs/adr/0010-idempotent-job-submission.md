# ADR 0010: Idempotent job submission

- Status: accepted
- Date: 2026-10-23

## Context

`POST /api/v1/jobs` starts work that costs tokens and money. A client whose request times out
cannot tell whether the job was created. If it retries, as every HTTP client library and CI
script eventually does, it creates a second job, a second budget reservation and a second bill.
The active-job limit (`429`) only caps the damage.

## Options considered

- **Deduplicate by body** (same spec within N minutes): this guesses intent. Two people
  legitimately submitting the same spec would get one job.
- **A client-chosen `Idempotency-Key` header (chosen)**: the convention of Stripe and the IETF
  `Idempotency-Key` HTTP header draft. The client decides what counts as "the same request".
- **A separate idempotency table with expiry**: more general, but the job row already is the
  stored response, and its lifetime is the natural lifetime of the key.

## Decision

- `Idempotency-Key` is optional: 1–128 characters from `[A-Za-z0-9._:-]`; anything else is a 422.
  Keys are scoped to the principal, so two API keys can use the same value.
- The job stores the key and a SHA-256 of the canonical JSON body, with a unique index on
  `(owner, idempotency_key)` (migration 0008). NULL keys never collide, on SQLite or PostgreSQL.
- A repeat with the same key and body returns the existing job's current state with `202` and
  `Idempotent-Replayed: true`. It creates nothing, takes no reservation, and is not refused by
  the active-job limit. The same key with a different body is a 422.
- Concurrent repeats are settled by the unique index rather than the lookup. The losing insert
  rolls back, together with its budget reservation, and its request returns the winner's job.
- A key is held for as long as its job exists; deleting the job frees the key.

## Consequences

- Tests:
  - a repeat returns the same job without hitting the active-job limit;
  - a different body is rejected;
  - keys are per owner, and malformed keys are rejected;
  - five concurrent submissions with one key create exactly one job (one created, four
    replayed);
  - deleting the job frees the key.
- Keys do not expire while their job exists, so a client that reuses keys for unrelated work
  has to delete old jobs or choose new keys. Per-request UUIDs, the usual practice, never meet
  this.
- The web UI does not send a key: its submit handler ignores repeat submits while a request
  is in flight. API and CLI clients benefit directly.
