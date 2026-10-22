"""Deterministic, network-free provider.

It answers each agent's request from the request's structured ``context`` using heuristics and
templates, so the full pipeline (and its tests, CI and demo) runs without an API key. Output is
a working FastAPI CRUD service; it does not attempt to understand arbitrary requirements the way
an LLM does.
"""

from __future__ import annotations

import asyncio
from typing import Any

from pydantic import BaseModel

from autoswe.llm.base import LLMOutputError, LLMRequest, LLMResponse, TokenUsage
from autoswe.llm.offline.analysis import analyze
from autoswe.llm.offline.blueprint import design
from autoswe.llm.offline.templates_app import render_project
from autoswe.llm.offline.templates_devops import render_devops
from autoswe.pipeline.artifacts import (
    ArchitectureSpec,
    FileBundle,
    RepairRequest,
    Requirements,
    ReviewFinding,
    ReviewReport,
    ValidationReport,
)
from autoswe.sandbox.models import SandboxResult, SandboxStatus


def _review(context: dict[str, Any]) -> ReviewReport:
    validation = ValidationReport.model_validate(context["validation"])
    requirements = Requirements.model_validate(context["requirements"])
    errors = len(validation.errors)
    warnings = sum(issue.severity == "warning" for issue in validation.issues)
    findings = [
        ReviewFinding(
            severity=issue.severity, file=issue.file, message=issue.message,
            suggestion=f"Address the '{issue.rule}' check.",
        )
        for issue in validation.issues
    ]  # fmt: skip
    findings.append(
        ReviewFinding(
            severity="info",
            file="app/main.py",
            message="Tables are created with metadata.create_all() at startup.",
            suggestion="Introduce Alembic migrations before the first schema change in production.",
        )
    )
    if any("Authentication" in item for item in requirements.out_of_scope):
        findings.append(
            ReviewFinding(
                severity="info",
                file=None,
                message="The API is unauthenticated because the spec did not ask for auth.",
                suggestion="Add OAuth2 or an authenticating gateway before exposing it publicly.",
            )
        )
    penalty, tested = _test_findings(context.get("tests"), findings)
    score = max(0, 100 - 15 * errors - 3 * warnings - penalty)
    verdict = "passes all structural checks" if errors == 0 else f"has {errors} blocking issue(s)"
    return ReviewReport(
        summary=(
            f"Deterministic review of {validation.files_checked} files: the project {verdict} "
            f"with {warnings} warning(s).{tested} Offline reviews do not judge business logic."
        ),
        score=score,
        findings=findings,
    )


def _test_findings(raw: object, findings: list[ReviewFinding]) -> tuple[int, str]:
    """Fold the sandbox result into the review: (score penalty, summary sentence)."""
    if not isinstance(raw, dict):
        return 0, ""  # tests were not executed (no sandbox configured)
    result = SandboxResult.model_validate(raw)
    if result.status is SandboxStatus.ERROR:
        findings.append(
            ReviewFinding(severity="info", file=None,
                          message=f"The test suite could not be executed: {result.message}",
                          suggestion="Check the sandbox runner; this says nothing about the code.")
        )  # fmt: skip
        return 0, " Its tests could not be executed."
    penalty = 0
    for name, check in (("ruff", result.lint), ("mypy", result.typecheck)):
        if check is not None and check.status == "failed":
            penalty += 3
            findings.append(
                ReviewFinding(severity="warning", file=None,
                              message=f"{name} reported {check.issue_count} issue(s).",
                              suggestion="; ".join(check.issues[:3]))
            )  # fmt: skip
    tests = result.tests
    if result.status is SandboxStatus.PASSED and tests is not None:
        return penalty, f" All {tests.passed} generated tests pass in the sandbox."
    findings.append(
        ReviewFinding(severity="error", file=None,
                      message=f"The generated test suite does not pass: {result.describe()}.",
                      suggestion="Fix the failing tests listed in the sandbox report.")
    )  # fmt: skip
    return penalty + 25, " Its generated tests do not pass in the sandbox."


class OfflineProvider:
    name = "offline"
    model = "offline-templates-v1"
    metered = False  # no model is called: nothing to bill or budget

    def __init__(self, delay_s: float = 0.0) -> None:
        # Optional pacing for demos (OFFLINE_DELAY_S); the answer itself does not change.
        self.delay_s = delay_s

    async def generate[T: BaseModel](self, request: LLMRequest[T]) -> LLMResponse[T]:
        if self.delay_s > 0:
            await asyncio.sleep(self.delay_s)
        context = dict(request.context)
        result: BaseModel
        match request.agent:
            case "analyst":
                result = analyze(str(context["spec"]))
            case "architect":
                result = design(Requirements.model_validate(context["requirements"]))
            case "coder":
                requirements = Requirements.model_validate(context["requirements"])
                architecture = ArchitectureSpec.model_validate(context["architecture"])
                result = FileBundle(
                    files=render_project(
                        requirements.project_name, requirements.summary, architecture
                    ),
                    notes="Rendered from offline templates.",
                )
            case "coder.repair":
                repair = RepairRequest.model_validate(context["repair"])
                # Templates are valid by construction; nothing to repair deterministically.
                result = FileBundle(files=repair.files, notes="Offline provider cannot repair.")
            case "devops":
                requirements = Requirements.model_validate(context["requirements"])
                architecture = ArchitectureSpec.model_validate(context["architecture"])
                result = FileBundle(files=render_devops(requirements.project_name, architecture))
            case "reviewer":
                result = _review(context)
            case other:
                raise LLMOutputError(f"offline provider has no strategy for agent '{other}'")

        if not isinstance(result, request.output_type):
            raise LLMOutputError(
                f"offline strategy for '{request.agent}' produced {type(result).__name__}, "
                f"expected {request.output_type.__name__}"
            )
        # No model is called, so no tokens are reported (rather than inventing an estimate).
        return LLMResponse(output=result, model=self.model, usage=TokenUsage())
