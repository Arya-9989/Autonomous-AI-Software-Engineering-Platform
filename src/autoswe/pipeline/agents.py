"""The five pipeline agents.

Each agent owns one stage: it renders a prompt from the current :class:`PipelineState`, asks the
job's LLM provider for a typed artifact, and folds the result back into the state. Agents are
stateless and know nothing about persistence or transport; the orchestrator handles that.
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, ClassVar, Protocol

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
from autoswe.pipeline.validation import Limits, path_key, sanitize_files, validate_project
from autoswe.sandbox.backends import Sandbox
from autoswe.sandbox.models import SandboxResult, SandboxStatus
from autoswe.tracing import mark_error, tracer

Emit = Callable[[str], Awaitable[None]]

PLATFORM_OWNED = frozenset({"ARCHITECTURE.md", "docs/architecture.json", "docs/requirements.json"})
DEVOPS_PREFIXES = ("Dockerfile", ".dockerignore", ".github/", "deploy/")
# Ownership is decided case-insensitively: "architecture.md" from the code generator would
# otherwise replace the platform's ARCHITECTURE.md once the zip is extracted on macOS or Windows.
_PLATFORM_KEYS = frozenset(path_key(path) for path in PLATFORM_OWNED)
_DEVOPS_KEYS = tuple(path_key(prefix) for prefix in DEVOPS_PREFIXES)


def is_devops_path(path: str) -> bool:
    return path_key(path).startswith(_DEVOPS_KEYS)


def is_platform_owned(path: str) -> bool:
    return path_key(path) in _PLATFORM_KEYS


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
    # Sandboxed test execution: None disables it (the reviewer then judges structure only).
    sandbox: Sandbox | None = None
    max_test_repair_rounds: int = 2
    test_runs: list[SandboxResult] = field(default_factory=list)
    test_repair_exhausted: bool = False

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
            if is_platform_owned(file.path) and stage is not StageName.ARCHITECT:
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

    @property
    def test_result(self) -> SandboxResult | None:
        return self.test_runs[-1] if self.test_runs else None

    def test_report(self) -> dict[str, Any] | None:
        """What is persisted and served at ``/jobs/{id}/tests``: the final run and the history."""
        final = self.test_result
        if final is None:
            return None
        return {
            "final": final.model_dump(mode="json"),
            "attempts": [{**run.brief(), "duration_s": run.duration_s} for run in self.test_runs],
            "repair_rounds": len(self.test_runs) - 1,
            "repair_budget_exhausted": self.test_repair_exhausted,
        }


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
                    metadata={"repair.kind": "validation", "repair.round": round_number},
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

        if state.sandbox is not None:
            usage += await self._test_and_repair(state, llm, emit, repaired)
            report = state.validation or report

        await emit("Reviewing correctness, security and operability")
        tests = state.test_result
        review_response = await llm.generate(
            LLMRequest(
                agent="reviewer",
                system=prompts.REVIEWER,
                prompt=prompts.render_payload(
                    requirements=requirements.model_dump(),
                    architecture=architecture.model_dump(),
                    validation=report.model_dump(),
                    tests=tests.brief() if tests is not None else "not executed",
                    files=[f.model_dump(include={"path", "content"}) for f in state.files.values()],
                ),
                output_type=ReviewReport,
                context={
                    "requirements": requirements.model_dump(),
                    "validation": report.model_dump(),
                    "files": sorted(state.files),
                    "tests": tests.model_dump(mode="json") if tests is not None else None,
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
        tested = "" if tests is None else f"; tests {_test_verdict(tests)}"
        return StageResult(
            summary=f"Validation {verdict}{tested}; review score {review.score}/100",
            usage=usage,
            files=tuple(sorted(repaired)),
        )

    async def _test_and_repair(
        self, state: PipelineState, llm: LLMProvider, emit: Emit, repaired: set[str]
    ) -> TokenUsage:
        """Run the tests in the sandbox; feed failures to the code generator, bounded."""
        assert state.sandbox is not None
        usage = TokenUsage()
        rounds = state.max_test_repair_rounds
        for attempt in range(rounds + 1):
            label = "" if attempt == 0 else f" (after repair round {attempt})"
            await emit(f"Running the test suite in the {state.sandbox.name} sandbox{label}")
            result = await _traced_sandbox_run(state, attempt)
            state.test_runs.append(result)
            await emit(result.describe())
            if result.status is SandboxStatus.PASSED or not result.repairable:
                break
            if attempt == rounds:
                state.test_repair_exhausted = rounds > 0
                if rounds:
                    await emit(f"Test repair budget of {rounds} round(s) used up")
                break
            sent = _files_for_test_repair(state, result)
            problems = result.problems()
            await emit(
                f"Test repair round {attempt + 1}: asking the code generator to fix "
                f"{len(problems)} problem(s) in {sorted(sent)[:6]}"
            )
            repair = RepairRequest(files=[state.files[p] for p in sorted(sent)], problems=problems)
            response = await llm.generate(
                LLMRequest(
                    agent="coder.repair",
                    system=prompts.TEST_REPAIR,
                    prompt=prompts.render_payload(
                        problems="\n\n".join(problems),
                        files=[f.model_dump() for f in repair.files],
                    ),
                    output_type=FileBundle,
                    context={"repair": repair.model_dump(), "kind": "tests"},
                    effort="high",
                    metadata={"repair.kind": "tests", "repair.round": attempt + 1},
                )
            )
            usage += response.usage
            changed = [
                f for f in response.output.files
                if f.path not in state.files or state.files[f.path].content != f.content
            ]  # fmt: skip
            allowed = {f.path for f in changed if f.path in sent or f.path not in state.files}
            accepted, _ = state.merge(changed, StageName.IMPLEMENT, keep=_owned_by(allowed))
            if not accepted:
                await emit("Test repair produced no usable changes; keeping the current files")
                break
            repaired.update(accepted)
            state.validate()
        return usage


async def _traced_sandbox_run(state: PipelineState, attempt: int) -> SandboxResult:
    assert state.sandbox is not None
    with tracer().start_as_current_span(
        "autoswe.sandbox.run",
        attributes={"autoswe.sandbox.backend": state.sandbox.name,
                    "autoswe.repair.round": attempt, "autoswe.sandbox.files": len(state.files)},
    ) as span:  # fmt: skip
        result = await state.sandbox.run(list(state.files.values()))
        tests = result.tests
        span.set_attribute("autoswe.sandbox.status", result.status.value)
        span.set_attribute("autoswe.sandbox.duration_s", result.duration_s)
        span.set_attribute("autoswe.sandbox.isolation", result.isolation)
        if result.limit:
            span.set_attribute("autoswe.sandbox.limit", result.limit)
        if tests is not None:
            span.set_attribute("autoswe.tests.collected", tests.collected)
            span.set_attribute("autoswe.tests.passed", tests.passed)
            span.set_attribute("autoswe.tests.failed", tests.failed + tests.errors)
        if result.status is SandboxStatus.ERROR:
            mark_error(span, result.message, "sandbox_error")
        return result


def _owned_by(allowed: set[str]) -> Callable[[str], bool]:
    """A test repair may change the files it was sent and add new non-delivery files."""
    frozen = frozenset(allowed)
    return lambda path: path in frozen and not is_devops_path(path)


_PY_PATH = re.compile(r"[A-Za-z0-9_./-]+\.py")
MAX_TEST_REPAIR_FILES = 12


def _files_for_test_repair(state: PipelineState, result: SandboxResult) -> set[str]:
    """The failing tests' files, files their tracebacks mention, then other source files."""
    picked: list[str] = []

    def add(path: str) -> None:
        if path in state.files and path not in picked and not is_devops_path(path):
            picked.append(path)

    for failure in result.tests.failures if result.tests else []:
        add(failure.nodeid.split("::", 1)[0])
        for match in _PY_PATH.findall(f"{failure.details} {failure.message}"):
            add(match.removeprefix("./"))
    for path in sorted(state.files):  # timeouts and limits name no file: send the sources
        if path.endswith(".py") and (path.startswith(("app/", "tests/")) or "/" not in path):
            add(path)
    return set(picked[:MAX_TEST_REPAIR_FILES])


def _test_verdict(result: SandboxResult) -> str:
    tests = result.tests
    if result.status is SandboxStatus.PASSED and tests is not None:
        return f"{tests.passed}/{tests.collected} passed"
    return result.status.value.replace("_", " ")


def _describe(report: ValidationReport) -> str:
    warnings = sum(issue.severity == "warning" for issue in report.issues)
    return (
        f"Validated {report.files_checked} files: {len(report.errors)} error(s), "
        f"{warnings} warning(s)"
    )


def default_agents() -> list[Agent]:
    return [RequirementsAnalyst(), Architect(), CodeGenerator(), DevOpsEngineer(), Reviewer()]


async def run_pipeline(
    state: PipelineState,
    llm: LLMProvider,
    emit: Emit,
    *,
    agents: list[Agent] | None = None,
    on_stage: Callable[[Agent, StageResult], Awaitable[None]] | None = None,
) -> list[StageResult]:
    """Run every agent in order without persistence (CLI, eval harness)."""
    results = []
    for agent in agents if agents is not None else default_agents():
        result = await agent.run(state, llm, emit)
        results.append(result)
        if on_stage is not None:
            await on_stage(agent, result)
    return results
