# ADR 0005: Publishing a generated project to a new GitHub repository

- Status: accepted
- Date: 2026-10-23

## Context

Users download a zip today. Pushing the project to a new repository with a pull request makes the
output reviewable where code is normally reviewed. It also means AutoSWE holds a credential that
can create repositories, which must not become a way to write anywhere else.

## Options considered

- **The caller's own token, per request.** Each user acts as themselves, but the API would receive
  long-lived credentials on every call, and a leak in logs or traces would expose them.
- **A server-side token** (fine-grained PAT, or a GitHub App installation token, which is used the
  same way), configured by the operator. Chosen: one credential, kept as a secret, scoped to one
  account or organisation.
- **`git push` from a subprocess.** Needs a git binary, a writable working copy and the token on a
  command line or in a credential helper. The REST Git Data API needs none of that.

## Decision

- `POST /api/v1/jobs/{id}/publish` with `{repository, visibility?, description?}`. The owner is
  the operator's (`GITHUB_PUBLISH_OWNER`, or the token's account); callers cannot choose it.
- Only a succeeded job, only by its owner (404 otherwise), at most once: a compare-and-set on
  `jobs.publication` (migration 0005) makes concurrent requests create one repository.
- Flow: create the repository with `auto_init`, wait for its default branch, commit every file in
  one tree request on a new `autoswe/<job>` branch, open a pull request. **A repository that
  already exists is refused, never reused**, so the token never writes to a repository this code
  did not just create. The default branch is left as GitHub initialised it.
- Least privilege, from GitHub's permissions table: Administration and Repository creation
  (write) to create the repository, Contents (write) for trees, commits and refs, **Workflows
  (write)** because every generated project contains `.github/workflows/ci.yml`, and Pull requests
  (write). Visibility defaults to private.
- The token lives in a `SecretStr`, is sent only in the `Authorization` header, is scrubbed from
  error text, and is never logged, stored or traced. `GITHUB_API_URL` must be https, except for
  loopback hosts or an explicit `GITHUB_API_INSECURE_HTTP=true` (for a fake on a private network).
- Timeouts on every call. Only requests that cannot duplicate work are retried (reads, and tree
  creation, which is content-addressed); repository and pull-request creation are not.
- Errors map to clear statuses: 409 for a taken name, 502 for a rejected token or a GitHub error,
  503 with `Retry-After` when rate-limited, 503 when publishing is not configured. A failure after
  the repository was created says where it is and is recorded on the job.
- Tests and the manual end-to-end check run against `tests/fake_github.py`, an in-memory fake of
  the endpoints used. No test or check creates a real repository; live mode starts only when an
  operator sets `GITHUB_PUBLISH_TOKEN`.

## Consequences

- Every published repository belongs to the operator's account or organisation, not to the
  principal who asked. Per-user tokens or a GitHub App with per-user installations would be the
  next step for multi-tenant use.
- A partly published job (the repository exists, the PR failed) is not retried automatically;
  the user finishes or deletes that repository by hand.
- The fake follows GitHub's documented behaviour but is not GitHub; the live path has not been run
  against github.com while building this.
