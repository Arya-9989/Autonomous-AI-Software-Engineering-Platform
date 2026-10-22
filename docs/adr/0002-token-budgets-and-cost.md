# ADR 0002: Per-principal token budgets and cost reporting

- Status: accepted
- Date: 2026-10-23

## Context

Each job makes five or more model calls, each up to `ANTHROPIC_MAX_TOKENS` of output plus a large
input. Without a ceiling, one API key can spend without limit, and nobody can see what a job or a
principal cost. The identity concept is the **principal**: the name attached to an API key in
`API_KEYS` (`anonymous` when auth is disabled); every job already belongs to one.

Requirements: track input, output and cache tokens per job and per principal; enforce budgets on
the server with a clear error; make concurrent jobs safe (two jobs must not both pass a check only
one fits under); compute cost from a pricing table kept in config; expose it over the API; persist
it with a migration.

## Options considered

1. **Check then spend.** Sum the principal's usage at submission and refuse when over. Two
   submissions that read the same total both pass, and a running job can spend far past the limit.
2. **A lock per principal** (`SELECT ... FOR UPDATE`, advisory locks). Works on PostgreSQL, not
   on SQLite (no row locks), and holding a lock for the length of a job is not an option.
3. **Reservations with compare-and-set (chosen).** Submission reserves a chunk of tokens with a
   single conditional `UPDATE principal_usage SET reserved = reserved + :n WHERE used + reserved
   + :n <= :limit`, in the same transaction that inserts the job. The database applies one
   conditional update at a time per row on both SQLite and PostgreSQL, so of two racing
   submissions at most the ones that fit succeed. Running jobs convert reservation into usage per
   call and top up with the same compare-and-set when they run out. This is the queue's existing
   claim pattern (`UPDATE ... WHERE status = 'queued'`), applied to money.
4. **Budgets in dollars.** Cost depends on the pricing table, which changes; a token budget is
   exact and provider-independent. Cost is reported next to it instead.

## Decision

- A `principal_usage` ledger row per (principal, period) holds `used_tokens`, `reserved_tokens`
  and the input, output, cache-read, cache-write, cost and call counters. Jobs and stage rows get
  cache-token and cost columns; jobs also record the reservation they hold, the period it came
  from, and a machine-readable `error_code`. Migration `0003`.
- Budgets: `TOKEN_BUDGET_DEFAULT` (empty means unlimited), per-principal `TOKEN_BUDGETS`,
  `TOKEN_BUDGET_PERIOD` (`month`, `day` or `total`, UTC) and `JOB_TOKEN_RESERVATION`. Tokens are
  input (cache included, as Anthropic bills it) plus output.
- At submission a refused reservation returns **HTTP 402** with
  `{"detail", "code": "token_budget_exceeded", "budget": {...}}` and creates no job. While a job
  runs, a refused top-up fails the job with `error_code = token_budget_exceeded` before the next
  call. A call already in flight finishes, so the overshoot is bounded by one call.
- Unused reservation is released in the transaction that ends the job: success, failure,
  cancellation of a queued job, or the stale-job reaper. A requeued job keeps its reservation and
  its cumulative token and cost totals (the interrupted attempt was billed).
- Metering wraps the job's provider (`MeteredProvider`): every call is priced per billed attempt,
  so a server-side fallback is priced at the model that served each attempt (`usage.iterations`).
- Prices live in `src/autoswe/pricing.toml` (replaceable with `PRICING_FILE`), in USD per million
  tokens, cited from the Anthropic claude-api skill's model table (cached 2026-10-06) and its
  prompt-caching multipliers. A model not in the table is *unpriced*: its tokens still count, and
  costs that include it are marked incomplete (`cost_complete: false`) instead of estimated.
- Read paths: `GET /api/v1/usage` (the caller's period, budget and cost), cost and cache fields on
  `GET /api/v1/jobs/{id}` and its stages, `GET /api/v1/pricing`, `autoswe usage` for operators, and
  Prometheus `autoswe_llm_cost_usd_total` and `autoswe_budget_rejections_total`.
- The offline provider declares `metered = False`: it bills nothing and is never budgeted.

## Consequences

- A principal's usage counts in the period its job was **submitted** in, even if it finishes after
  the period rolls over. Simple and consistent with where its reservation was taken.
- The reservation size is a trade-off: large values refuse jobs that would have fit, small values
  mean more top-up writes. It is clamped to the budget, so a budget smaller than one reservation
  still admits a job.
- The tokens of a call that fails mid-stream are not known to us and are not counted.
- Verified by `tests/test_budget.py`, including 10 concurrent submissions against a budget that
  fits 3 (exactly 3 accepted) on SQLite and 25 against 5 on PostgreSQL (CI runs a Postgres
  service), a job that stops mid-run when its top-up is refused, and reservation release on every
  terminal path.
