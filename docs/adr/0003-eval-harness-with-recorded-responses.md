# ADR 0003: An eval harness that gates CI on recorded model responses

- Status: accepted
- Date: 2026-10-23

## Context

Prompt, schema, validator and template changes can make generated projects worse without any unit
test failing. We need a fixed suite of specs scored on things that can be checked objectively,
with thresholds that fail CI on a regression. CI has no API key and must be deterministic; a
developer with a key must be able to run the same suite live.

## Options considered

1. **LLM-as-judge scoring.** Needs a key in CI, costs money per run and is not deterministic.
2. **Live calls in CI with a key.** Flaky, slow, costly, and unavailable to forks.
3. **HTTP-level cassettes** (record the Messages API's raw responses). Couples the recordings to
   SDK and transport details that change for reasons unrelated to quality.
4. **Recorded structured answers at the provider boundary (chosen).** Every agent asks the
   `LLMProvider` for a typed artifact; recording what came back at that boundary, together with a
   fingerprint of what was asked, replays the whole pipeline (validation, sandbox, repair,
   review) for real while making the model's part fixed.

## Decision

- Fixtures (`evals/fixtures/*.yaml`) hold a spec and expectations written **from the spec**:
  entities, fields, endpoints, entities that must not be invented, a minimum number of tests.
- Metrics are all objective and in [0, 1]: pipeline completed, structural validation passed,
  generated tests passed in the sandbox, test pass rate, ruff clean, mypy clean, expected endpoints
  *served by the code* (FastAPI routes read statically with `ast`, mounts and prefixes resolved),
  designed endpoints implemented, entity and field recall, no spurious entities, and the
  reviewer's score. A metric that could not be measured is `null`, and a thresholded `null`
  fails the gate.
- Recordings (`evals/recordings/<fixture>.json`) store each call's agent, a SHA-256 of
  (agent, system prompt, user prompt, output schema), the model, the token usage and the answer.
  Replay refuses a call whose fingerprint differs, a missing call and an unused call, so a
  changed prompt or upstream output can never silently replay stale answers.
- Every recording carries `source` and `provenance`. The checked-in recordings were produced by
  the deterministic offline provider and say so in plain words; they are not, and do not claim to
  be, Claude output. `autoswe eval record --source anthropic` refreshes them from the live API,
  and `autoswe eval run --live` scores the live model without recordings.
- `evals/thresholds.yaml` pins the measured baseline. CI builds the sandbox image, runs
  `autoswe eval run --sandbox docker`, writes Markdown and JSON reports, and publishes the
  Markdown to the job summary.

## Consequences

- With offline-provider recordings the gate measures the deterministic pipeline (validators,
  sandbox, routes, templates) end to end, and it catches prompt and schema drift through the
  fingerprints. It does not measure Claude's quality until someone records with a key.
- The reviewer's prompt contains the sandbox verdicts, so a recording replays only under a sandbox
  that produces the same verdicts (any working Docker or local sandbox does for passing suites).
- The clinic fixture deliberately scores below 1 on entity and endpoint recall; the thresholds
  record that as the baseline so it is tracked, not hidden.
