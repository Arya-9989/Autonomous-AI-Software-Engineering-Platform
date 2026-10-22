"""Run the eval suite (replayed or live), record responses, and write the report."""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from autoswe import __version__
from autoswe.evals.fixtures import EvalFixture
from autoswe.evals.recording import Recording, RecordingProvider, ReplayProvider, Source
from autoswe.evals.scoring import (
    METRICS,
    FixtureResult,
    Thresholds,
    check,
    score,
    suite_means,
)
from autoswe.llm.base import LLMError, LLMProvider, LLMRequest, LLMResponse, TokenUsage
from autoswe.pipeline.agents import PipelineState, run_pipeline
from autoswe.sandbox.backends import Sandbox


class _Counting:
    """Counts calls and tokens of whatever provider the suite runs on."""

    def __init__(self, inner: LLMProvider) -> None:
        self._inner = inner
        self.name, self.model = inner.name, inner.model
        self.calls = 0
        self.usage = TokenUsage()

    async def generate[T: BaseModel](self, request: LLMRequest[T]) -> LLMResponse[T]:
        response = await self._inner.generate(request)
        self.calls += 1
        self.usage += response.usage
        return response


@dataclass
class SuiteRun:
    mode: str  # "replay" or "live"
    source: str  # where the answers came from
    provenance: str
    model: str
    sandbox: str
    sandbox_isolation: str
    results: list[FixtureResult]
    started_at: datetime
    duration_s: float

    def means(self) -> dict[str, float | None]:
        return suite_means(self.results)


async def run_fixture(
    fixture: EvalFixture, llm: LLMProvider, sandbox: Sandbox | None, repair_rounds: int
) -> FixtureResult:
    counting = _Counting(llm)
    state = PipelineState(spec=fixture.spec, sandbox=sandbox, max_test_repair_rounds=repair_rounds)

    async def quiet(_: str) -> None:
        return None

    started = time.perf_counter()
    error = None
    try:
        await run_pipeline(state, counting, quiet)
    except LLMError as exc:  # a provider failure or a stale recording: scored, not raised
        error = f"{type(exc).__name__}: {exc}"
    if isinstance(llm, ReplayProvider) and error is None and llm.unused_calls:
        error = f"RecordingMismatchError: {llm.unused_calls} recorded call(s) were never made"
    result = score(fixture, state, error)
    return result.model_copy(
        update={
            "model_calls": counting.calls,
            "input_tokens": counting.usage.input_tokens,
            "output_tokens": counting.usage.output_tokens,
            "duration_s": round(time.perf_counter() - started, 2),
        }
    )


def _isolation(sandbox: Sandbox | None) -> str:
    return "tests not executed" if sandbox is None else str(getattr(sandbox, "isolation", ""))


async def run_suite(
    fixtures: list[EvalFixture],
    provider_for: Callable[[EvalFixture], LLMProvider],
    *,
    sandbox: Sandbox | None,
    repair_rounds: int,
    mode: str,
    source: str,
    provenance: str,
) -> SuiteRun:
    started_at = datetime.now(UTC).replace(microsecond=0)
    started = time.perf_counter()
    results = []
    model = ""
    for fixture in fixtures:
        llm = provider_for(fixture)
        model = llm.model
        results.append(await run_fixture(fixture, llm, sandbox, repair_rounds))
    return SuiteRun(
        mode=mode, source=source, provenance=provenance, model=model,
        sandbox=sandbox.name if sandbox else "none", sandbox_isolation=_isolation(sandbox),
        results=results, started_at=started_at,
        duration_s=round(time.perf_counter() - started, 1),
    )  # fmt: skip


def load_recordings(recordings: Path, fixtures: list[EvalFixture]) -> dict[str, Recording]:
    loaded = {}
    for fixture in fixtures:
        path = recordings / f"{fixture.name}.json"
        if not path.is_file():
            raise FileNotFoundError(
                f"no recording for fixture '{fixture.name}' at {path}; run `autoswe eval record`"
            )
        loaded[fixture.name] = Recording.load(path)
    return loaded


async def record_suite(
    fixtures: list[EvalFixture],
    inner: LLMProvider,
    *,
    source: Source,
    sandbox: Sandbox | None,
    repair_rounds: int,
    recordings: Path,
) -> list[Path]:
    written = []
    for fixture in fixtures:
        recorder = RecordingProvider(inner)
        state = PipelineState(
            spec=fixture.spec, sandbox=sandbox, max_test_repair_rounds=repair_rounds
        )

        async def quiet(_: str) -> None:
            return None

        await run_pipeline(state, recorder, quiet)  # a failure here must not write a recording
        path = recordings / f"{fixture.name}.json"
        recorder.recording(fixture.name, source, sandbox.name if sandbox else "none").save(path)
        written.append(path)
    return written


# --- report ---------------------------------------------------------------------------------------


def _fmt(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.3f}"


def report_json(run: SuiteRun, thresholds: Thresholds, failures: list[str]) -> dict[str, Any]:
    return {
        "autoswe_version": __version__,
        "passed": not failures,
        "failures": failures,
        "mode": run.mode,
        "source": run.source,
        "provenance": run.provenance,
        "model": run.model,
        "sandbox": run.sandbox,
        "sandbox_isolation": run.sandbox_isolation,
        "started_at": run.started_at.isoformat(),
        "duration_s": run.duration_s,
        "means": run.means(),
        "thresholds": thresholds.metrics,
        "fixtures": [r.model_dump(mode="json") for r in run.results],
    }


_COLUMNS = (
    ("completed", "done"), ("validation_passed", "valid"), ("tests_passed", "tests"),
    ("lint_clean", "lint"), ("typecheck_clean", "types"), ("endpoint_recall", "endpoints"),
    ("design_coverage", "design"), ("entity_recall", "entities"), ("field_recall", "fields"),
    ("no_spurious_entities", "no spurious"), ("review_score", "review"),
)  # fmt: skip


def report_markdown(run: SuiteRun, thresholds: Thresholds, failures: list[str]) -> str:
    means = run.means()
    verdict = "PASS" if not failures else f"FAIL ({len(failures)} regression(s))"
    what = (
        f"replay of recorded responses (source: `{run.source}`)"
        if run.mode == "replay"
        else f"live run against `{run.model}`"
    )
    lines = [
        "# AutoSWE eval report",
        "",
        f"**Result: {verdict}** | {len(run.results)} fixtures | {what} | "
        f"sandbox `{run.sandbox}` | {run.duration_s} s",
        "",
        f"> {run.provenance}",
        "",
        f"Sandbox isolation: {run.sandbox_isolation}",
        "",
        "## Suite means",
        "",
        "| Metric | Mean | Threshold | | What it measures |",
        "| --- | --- | --- | --- | --- |",
    ]
    for name, description in METRICS.items():
        minimum = thresholds.metrics.get(name)
        value = means.get(name)
        status = ""
        if minimum is not None:
            status = "ok" if value is not None and value + 1e-9 >= minimum else "**regressed**"
        limit = "" if minimum is None else f"{minimum:.3f}"
        lines.append(f"| `{name}` | {_fmt(value)} | {limit} | {status} | {description} |")
    lines += ["", "## Fixtures", ""]
    lines.append("| Fixture | " + " | ".join(label for _, label in _COLUMNS) + " | tests run |")
    lines.append("| --- " * (len(_COLUMNS) + 2) + "|")
    for result in run.results:
        cells = [_fmt(result.metrics.get(name)) for name, _ in _COLUMNS]
        tests = f"{result.tests_passed}/{result.tests_collected}" if result.test_status else "n/a"
        lines.append(f"| `{result.name}` | " + " | ".join(cells) + f" | {tests} |")
    notes = []
    for result in run.results:
        details = []
        if result.error:
            details.append(f"error: {result.error}")
        if result.missing_entities:
            details.append(f"missing entities: {', '.join(result.missing_entities)}")
        if result.spurious_entities:
            details.append(f"spurious entities: {', '.join(result.spurious_entities)}")
        if result.missing_endpoints:
            details.append(f"missing endpoints: {', '.join(result.missing_endpoints)}")
        if result.test_repair_rounds:
            details.append(f"test repair rounds: {result.test_repair_rounds}")
        if details:
            notes.append(f"- `{result.name}`: " + "; ".join(details))
    if notes:
        lines += ["", "## Findings", "", *notes]
    if failures:
        lines += ["", "## Regressions", "", *(f"- {failure}" for failure in failures)]
    return "\n".join(lines) + "\n"


def write_report(run: SuiteRun, thresholds: Thresholds, out: Path) -> list[str]:
    failures = check(run.means(), thresholds)
    out.mkdir(parents=True, exist_ok=True)
    (out / "eval-report.json").write_text(
        json.dumps(report_json(run, thresholds, failures), indent=2) + "\n", encoding="utf-8"
    )
    (out / "eval-report.md").write_text(
        report_markdown(run, thresholds, failures), encoding="utf-8"
    )
    return failures
