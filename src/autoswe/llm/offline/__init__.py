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
    score = max(0, 100 - 15 * errors - 3 * warnings)
    verdict = "passes all structural checks" if errors == 0 else f"has {errors} blocking issue(s)"
    return ReviewReport(
        summary=(
            f"Deterministic review of {validation.files_checked} files: the project {verdict} "
            f"with {warnings} warning(s). Offline reviews do not judge business logic."
        ),
        score=score,
        findings=findings,
    )


class OfflineProvider:
    name = "offline"
    model = "offline-templates-v1"

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
