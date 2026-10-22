"""Objective metrics for one fixture run, suite means, and the regression gate.

Every metric is a number in [0, 1] computed from artifacts, never from a model's opinion
(``review_score`` is reported as the reviewer's own 0-100 score, scaled). A metric that could
not be measured (sandbox metrics without a sandbox, fields without expectations) is ``None`` and
never counts as a pass: the gate fails when a thresholded metric was not measured.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from autoswe.evals.fixtures import EvalFixture
from autoswe.evals.routes import implemented_endpoints, normalize_endpoint, parse_endpoint
from autoswe.pipeline.agents import PipelineState
from autoswe.sandbox.models import CheckResult, SandboxStatus

METRICS: dict[str, str] = {
    "completed": "The pipeline finished without a provider or replay error",
    "validation_passed": "Structural validation found no errors",
    "tests_passed": "Every generated test passed in the sandbox (and at least min_tests ran)",
    "test_pass_rate": "Passed / collected tests in the sandbox",
    "lint_clean": "ruff check passed in the sandbox",
    "typecheck_clean": "mypy passed in the sandbox",
    "endpoint_recall": "Expected endpoints from the spec that the code actually serves",
    "design_coverage": "Endpoints in the architecture that the code actually serves",
    "entity_recall": "Expected entities present in the requirements",
    "field_recall": "Expected entity fields present in the requirements",
    "no_spurious_entities": "No entity the spec rules out was invented",
    "review_score": "The reviewer's score / 100",
}
SANDBOX_METRICS = frozenset({"tests_passed", "test_pass_rate", "lint_clean", "typecheck_clean"})


class FixtureResult(BaseModel):
    name: str
    metrics: dict[str, float | None]
    error: str | None = None
    test_status: str | None = None
    tests_collected: int = 0
    tests_passed: int = 0
    test_repair_rounds: int = 0
    files: int = 0
    model_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_input_tokens: int = 0
    cost_usd: float = 0.0
    cost_complete: bool = True
    duration_s: float = 0.0
    missing_endpoints: list[str] = Field(default_factory=list)
    missing_entities: list[str] = Field(default_factory=list)
    spurious_entities: list[str] = Field(default_factory=list)


def _ratio(found: int, wanted: int) -> float | None:
    return None if wanted == 0 else found / wanted


def _check_passed(check: CheckResult | None) -> float | None:
    return None if check is None else float(check.status == "passed")


def _entity_metrics(
    fixture: EvalFixture, state: PipelineState
) -> tuple[dict[str, float | None], list[str], list[str]]:
    expected = fixture.expected
    found = state.requirements.entities if state.requirements else []
    entities = {entity.name.casefold(): entity for entity in found}
    missing = [name for name in expected.entities if name.casefold() not in entities]
    spurious = [name for name in expected.spurious_entities if name.casefold() in entities]
    hits = total = 0
    for name, wanted in expected.fields.items():
        entity = entities.get(name.casefold())
        present = {field.name for field in entity.fields} if entity else set()
        total += len(wanted)
        hits += len(present & set(wanted))
    metrics = {
        "entity_recall": _ratio(len(expected.entities) - len(missing), len(expected.entities)),
        "field_recall": _ratio(hits, total),
        "no_spurious_entities": float(not spurious) if expected.spurious_entities else None,
    }
    return metrics, missing, spurious


def score(fixture: EvalFixture, state: PipelineState, error: str | None) -> FixtureResult:
    expected = fixture.expected
    files = {path: f.content for path, f in state.files.items()}
    served = implemented_endpoints(files)
    wanted = {parse_endpoint(e) for e in expected.endpoints}
    architecture = state.architecture
    designed = {
        normalize_endpoint(e.method, e.path)
        for e in (architecture.endpoints if architecture else [])
    }
    entity_metrics, missing_entities, spurious = _entity_metrics(fixture, state)

    metrics: dict[str, float | None] = dict.fromkeys(METRICS)
    metrics.update(entity_metrics)
    metrics["completed"] = 0.0 if error else 1.0
    validation = state.validation
    metrics["validation_passed"] = None if validation is None else float(validation.passed)
    metrics["endpoint_recall"] = _ratio(len(wanted & served), len(wanted))
    metrics["design_coverage"] = _ratio(len(designed & served), len(designed))
    metrics["review_score"] = None if state.review is None else state.review.score / 100

    result = state.test_result
    tests = result.tests if result is not None else None
    collected = tests.collected if tests else 0
    passed = tests.passed if tests else 0
    if result is not None and result.status is not SandboxStatus.ERROR:
        enough = collected >= expected.min_tests
        metrics["tests_passed"] = float(result.status is SandboxStatus.PASSED and enough)
        metrics["test_pass_rate"] = _ratio(passed, collected) or 0.0
        metrics["lint_clean"] = _check_passed(result.lint)
        metrics["typecheck_clean"] = _check_passed(result.typecheck)
    if error:
        # A run that did not finish scores zero on what it did not produce; sandbox metrics
        # count as zero too when a sandbox was configured (they could have been measured).
        for name, value in metrics.items():
            if value is None and (name not in SANDBOX_METRICS or state.sandbox is not None):
                metrics[name] = 0.0
    report = state.test_report()
    return FixtureResult(
        name=fixture.name,
        metrics=metrics,
        error=error,
        test_status=result.status.value if result else None,
        tests_collected=collected,
        tests_passed=passed,
        test_repair_rounds=int(report["repair_rounds"]) if report else 0,
        files=len(files),
        missing_endpoints=sorted(wanted - served),
        missing_entities=missing_entities,
        spurious_entities=spurious,
    )


class Thresholds(BaseModel):
    """Suite-level minimums, checked into the repository (``evals/thresholds.yaml``)."""

    model_config = ConfigDict(extra="forbid")

    metrics: dict[str, float]

    @model_validator(mode="after")
    def _known(self) -> Thresholds:
        unknown = set(self.metrics) - set(METRICS)
        if unknown:
            raise ValueError(f"unknown metric(s) in thresholds: {sorted(unknown)}")
        for name, value in self.metrics.items():
            if not 0 <= value <= 1:
                raise ValueError(f"threshold for {name} must be within [0, 1]")
        return self

    @classmethod
    def load(cls, path: Path) -> Thresholds:
        return cls.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))


def suite_means(results: list[FixtureResult]) -> dict[str, float | None]:
    means: dict[str, float | None] = {}
    for name in METRICS:
        values = [r.metrics[name] for r in results if r.metrics.get(name) is not None]
        measured = [value for value in values if value is not None]
        means[name] = round(sum(measured) / len(measured), 4) if measured else None
    return means


def check(means: dict[str, float | None], thresholds: Thresholds) -> list[str]:
    """Regressions as human-readable lines; empty when the gate passes."""
    failures = []
    for name, minimum in thresholds.metrics.items():
        value = means.get(name)
        if value is None:
            hint = (
                " (needs a sandbox: --sandbox docker or local)" if name in SANDBOX_METRICS else ""
            )
            failures.append(f"{name}: not measured{hint}; threshold {minimum:.3f}")
        elif value + 1e-9 < minimum:
            failures.append(f"{name}: {value:.3f} is below the threshold {minimum:.3f}")
    return failures


def as_dict(results: list[FixtureResult]) -> list[dict[str, Any]]:
    return [r.model_dump(mode="json") for r in results]
