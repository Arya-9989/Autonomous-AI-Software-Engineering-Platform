"""The five pipeline agents.

Each agent owns one stage: it renders a prompt from the current :class:`PipelineState`, asks the
job's LLM provider for a typed artifact, and folds the result back into the state. Agents are
stateless and know nothing about persistence or transport; the orchestrator handles that.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import ClassVar, Protocol

from autoswe.llm.base import LLMProvider, LLMRequest, TokenUsage
from autoswe.pipeline import prompts
from autoswe.pipeline.artifacts import (
    ArchitectureSpec,
    FileBundle,
    GeneratedFile,
    RepairRequest,
    Requirements,
    ReviewReport,
    StageName,
    ValidationIssue,
    ValidationReport,
)
from autoswe.pipeline.docs import architecture_markdown
from autoswe.pipeline.validation import Limits, sanitize_files, validate_project

Emit = Callable[[str], Awaitable[None]]

PLATFORM_OWNED = frozenset({"ARCHITECTURE.md", "docs/architecture.json", "docs/requirements.json"})
DEVOPS_PREFIXES = ("Dockerfile", ".dockerignore", ".github/", "deploy/")


def is_devops_path(path: str) -> bool:
    return path.startswith(DEVOPS_PREFIXES)


@dataclass
class PipelineState:
    spec: str
    limits: Limits = field(default_factory=Limits)
    max_repair_rounds: int = 1
    requirements: Requirements | None = None
    architecture: ArchitectureSpec | None = None
    files: dict[str, GeneratedFile] = field(default_factory=dict)
    file_origin: dict[str, StageName] = field(default_factory=dict)
    validation: ValidationReport | None = None
    review: ReviewReport | None = None
    rejected: list[ValidationIssue] = field(default_factory=list)

    def require_requirements(self) -> Requirements:
        if self.requirements is None:
            raise RuntimeError("requirements stage has not run")
        return self.requirements

    def require_architecture(self) -> ArchitectureSpec:
        if self.architecture is None:
            raise RuntimeError("architecture stage has not run")
        return self.architecture

    def merge(
        self, files: list[GeneratedFile], stage: StageName, keep: Callable[[str], bool]
    ) -> tuple[list[str], list[str]]:
        """Merge files produced by ``stage``; returns (accepted paths, dropped paths)."""
        clean, issues = sanitize_files(files, self.limits)
        self.rejected.extend(issues)
        accepted, dropped = [], []
        for file in clean:
            if file.path in PLATFORM_OWNED and stage is not StageName.ARCHITECT:
                dropped.append(file.path)
            elif keep(file.path):
                self.files[file.path] = file
                self.file_origin[file.path] = stage
                accepted.append(file.path)
            else:
                dropped.append(file.path)
        return accepted, dropped

    def validate(self) -> ValidationReport:
        report = validate_project(list(self.files.values()), self.architecture)
        report = report.model_copy(update={"issues": [*self.rejected, *report.issues]})
        self.validation = report
        return report


@dataclass(frozen=True, slots=True)
class StageResult:
    summary: str
    usage: TokenUsage = field(default_factory=TokenUsage)
    files: tuple[str, ...] = ()


class Agent(Protocol):
    stage: ClassVar[StageName]

    async def run(self, state: PipelineState, llm: LLMProvider, emit: Emit) -> StageResult: ...


class RequirementsAnalyst:
    stage: ClassVar[StageName] = StageName.ANALYZE

    async def run(self, state: PipelineState, llm: LLMProvider, emit: Emit) -> StageResult:
        await emit("Extracting entities, user stories and constraints from the spec")
        response = await llm.generate(
            LLMRequest(
                agent="analyst",
                system=prompts.ANALYST,
                prompt=prompts.render_payload(specification=state.spec),
                output_type=Requirements,
                context={"spec": state.spec},
                effort="medium",
            )
        )
        state.requirements = response.output
        names = ", ".join(e.name for e in response.output.entities) or "none"
        return StageResult(
            summary=(
                f"{len(response.output.functional_requirements)} functional requirements; "
                f"entities: {names}"
            ),
            usage=response.usage,
        )


class Architect:
    stage: ClassVar[StageName] = StageName.ARCHITECT

    async def run(self, state: PipelineState, llm: LLMProvider, emit: Emit) -> StageResult:
        requirements = state.require_requirements()
        await emit("Designing components, endpoints and deployment topology")
        response = await llm.generate(
            LLMRequest(
                agent="architect",
                system=prompts.ARCHITECT,
                prompt=prompts.render_payload(requirements=requirements.model_dump()),
                output_type=ArchitectureSpec,
                context={"requirements": requirements.model_dump()},
                effort="high",
            )
        )
        architecture = response.output
        state.architecture = architecture
        docs = [
            GeneratedFile(
                path="ARCHITECTURE.md",
                content=architecture_markdown(requirements, architecture),
                description="Architecture overview rendered by the platform",
            ),
            GeneratedFile(
                path="docs/architecture.json",
                content=architecture.model_dump_json(indent=2),
                description="Machine-readable architecture spec",
            ),
            GeneratedFile(
                path="docs/requirements.json",
                content=requirements.model_dump_json(indent=2),
                description="Machine-readable requirements",
            ),
        ]
        accepted, _ = state.merge(docs, self.stage, keep=lambda _: True)
        return StageResult(
            summary=(
                f"{len(architecture.components)} components, {len(architecture.endpoints)} "
                f"endpoints, {len(architecture.decisions)} decision records"
            ),
            usage=response.usage,
            files=tuple(accepted),
        )


class CodeGenerator:
    stage: ClassVar[StageName] = StageName.IMPLEMENT

    async def run(self, state: PipelineState, llm: LLMProvider, emit: Emit) -> StageResult:
        requirements = state.require_requirements()
        architecture = state.require_architecture()
        await emit("Writing application code and tests")
        response = await llm.generate(
            LLMRequest(
                agent="coder",
                system=prompts.CODER,
                prompt=prompts.render_payload(
                    requirements=requirements.model_dump(),
                    architecture=architecture.model_dump(),
                ),
                output_type=FileBundle,
                context={
                    "requirements": requirements.model_dump(),
                    "architecture": architecture.model_dump(),
                },
                effort="high",
            )
        )
        accepted, dropped = state.merge(
            response.output.files, self.stage, keep=lambda p: not is_devops_path(p)
        )
        if dropped:
            await emit(f"Ignored {len(dropped)} file(s) owned by another stage: {dropped[:5]}")
        return StageResult(
            summary=f"Generated {len(accepted)} source and test files",
            usage=response.usage,
            files=tuple(accepted),
        )


class DevOpsEngineer:
    stage: ClassVar[StageName] = StageName.DEVOPS

    async def run(self, state: PipelineState, llm: LLMProvider, emit: Emit) -> StageResult:
        requirements = state.require_requirements()
        architecture = state.require_architecture()
        await emit("Containerising and writing CI, Kubernetes and Terraform")
        file_list = sorted(state.files)
        response = await llm.generate(
            LLMRequest(
                agent="devops",
                system=prompts.DEVOPS,
                prompt=prompts.render_payload(
                    requirements=requirements.model_dump(),
                    deployment=architecture.deployment.model_dump(),
                    repository_files=file_list,
                ),
                output_type=FileBundle,
                context={
                    "requirements": requirements.model_dump(),
                    "architecture": architecture.model_dump(),
                    "files": file_list,
                },
                effort="medium",
            )
        )
        accepted, dropped = state.merge(response.output.files, self.stage, keep=is_devops_path)
        if dropped:
            await emit(f"Ignored {len(dropped)} non-delivery file(s): {dropped[:5]}")
        return StageResult(
            summary=f"Generated {len(accepted)} delivery files (Docker, CI, K8s, Terraform)",
            usage=response.usage,
            files=tuple(accepted),
        )


class Reviewer:
    """Validates the project, runs bounded repair rounds, then asks for a qualitative review."""

    stage: ClassVar[StageName] = StageName.REVIEW

    async def run(self, state: PipelineState, llm: LLMProvider, emit: Emit) -> StageResult:
        requirements = state.require_requirements()
        architecture = state.require_architecture()
        usage = TokenUsage()
        repaired: set[str] = set()

        report = state.validate()
        await emit(_describe(report))
        for round_number in range(1, state.max_repair_rounds + 1):
            failing = sorted({i.file for i in report.errors if i.file and i.file in state.files})
            if not failing:
                break
            await emit(f"Repair round {round_number}: asking the code generator to fix {failing}")
            repair = RepairRequest(
                files=[state.files[path] for path in failing],
                problems=[
                    f"{i.file}:{i.line or '-'} [{i.rule}] {i.message}" for i in report.errors
                ],
            )
            response = await llm.generate(
                LLMRequest(
                    agent="coder.repair",
                    system=prompts.REPAIR,
                    prompt=prompts.render_payload(
                        problems="\n".join(repair.problems),
                        files=[f.model_dump() for f in repair.files],
                    ),
                    output_type=FileBundle,
                    context={"repair": repair.model_dump()},
                    effort="high",
                )
            )
            usage += response.usage
            changed = [
                f for f in response.output.files
                if f.path in failing and state.files[f.path].content != f.content
            ]  # fmt: skip
            if not changed:
                await emit("Repair produced no changes; keeping the current files")
                break
            for file in changed:
                stage = state.file_origin.get(file.path, StageName.IMPLEMENT)
                state.merge([file], stage, keep=lambda _: True)
                repaired.add(file.path)
            report = state.validate()
            await emit(_describe(report))

        await emit("Reviewing correctness, security and operability")
        review_response = await llm.generate(
            LLMRequest(
                agent="reviewer",
                system=prompts.REVIEWER,
                prompt=prompts.render_payload(
                    requirements=requirements.model_dump(),
                    architecture=architecture.model_dump(),
                    validation=report.model_dump(),
                    files=[f.model_dump(include={"path", "content"}) for f in state.files.values()],
                ),
                output_type=ReviewReport,
                context={
                    "requirements": requirements.model_dump(),
                    "validation": report.model_dump(),
                    "files": sorted(state.files),
                },
                effort="medium",
            )
        )
        usage += review_response.usage
        review = review_response.output.model_copy(
            update={"score": max(0, min(100, review_response.output.score))}
        )
        state.review = review
        verdict = "passed" if report.passed else f"{len(report.errors)} blocking issue(s)"
        return StageResult(
            summary=f"Validation {verdict}; review score {review.score}/100",
            usage=usage,
            files=tuple(sorted(repaired)),
        )


def _describe(report: ValidationReport) -> str:
    warnings = sum(issue.severity == "warning" for issue in report.issues)
    return (
        f"Validated {report.files_checked} files: {len(report.errors)} error(s), "
        f"{warnings} warning(s)"
    )


def default_agents() -> list[Agent]:
    return [RequirementsAnalyst(), Architect(), CodeGenerator(), DevOpsEngineer(), Reviewer()]
