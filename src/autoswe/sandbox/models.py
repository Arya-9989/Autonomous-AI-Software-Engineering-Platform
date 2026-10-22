"""Results of running a generated project's checks and tests in a sandbox.

The sandbox reports what happened as data, never as an exception: a failing test, an infinite
loop and a fork bomb are all ordinary outcomes of running untrusted code. Only the backend
itself breaking (Docker unreachable, image missing) is reported as ``error``.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class SandboxStatus(StrEnum):
    PASSED = "passed"  # the test suite ran and every collected test passed
    FAILED = "failed"  # tests failed, errored or could not be collected
    NO_TESTS = "no_tests"  # pytest collected nothing
    TIMEOUT = "timeout"  # the wall-clock limit was reached
    RESOURCE_LIMIT = "resource_limit"  # memory, pids or output limit was hit
    ERROR = "error"  # the sandbox itself failed; says nothing about the code

    @property
    def repairable(self) -> bool:
        """Outcomes caused by the generated code, which a repair round can address."""
        return self in {SandboxStatus.FAILED, SandboxStatus.TIMEOUT, SandboxStatus.RESOURCE_LIMIT}


LimitKind = Literal["memory", "pids", "output", "archive"]
CheckStatus = Literal["passed", "failed", "skipped", "timeout", "error"]


class _Model(BaseModel):
    model_config = ConfigDict(extra="ignore")


class CheckResult(_Model):
    """A static check (ruff, mypy) run inside the sandbox."""

    status: CheckStatus
    issue_count: int = 0
    issues: list[str] = Field(default_factory=list, description="First issues, one per line")
    duration_s: float = 0.0


class TestFailure(_Model):
    __test__ = False  # not a pytest test class

    nodeid: str
    phase: Literal["collect", "setup", "call", "teardown"]
    message: str
    details: str = ""


class TestSummary(_Model):
    __test__ = False

    exit_code: int | None = None
    collected: int = 0
    passed: int = 0
    failed: int = 0
    errors: int = 0
    skipped: int = 0
    duration_s: float = 0.0
    failures: list[TestFailure] = Field(default_factory=list)
    output_tail: str = ""


class SandboxResult(_Model):
    status: SandboxStatus
    backend: str
    isolation: str = Field(description="What the backend isolates, in plain words")
    limit: LimitKind | None = None
    message: str = ""
    duration_s: float = 0.0
    exit_code: int | None = None
    lint: CheckResult | None = None
    typecheck: CheckResult | None = None
    tests: TestSummary | None = None

    @property
    def repairable(self) -> bool:
        return self.status.repairable

    def problems(self, max_failures: int = 10) -> list[str]:
        """Failure descriptions for a repair prompt, most useful first."""
        if self.status is SandboxStatus.TIMEOUT:
            return [
                f"The test run exceeded its wall-clock limit ({self.message}). Look for "
                "infinite loops, blocking network calls or sleeps in the code and tests."
            ]
        if self.status is SandboxStatus.RESOURCE_LIMIT:
            return [
                f"The test run hit the sandbox {self.limit} limit ({self.message}). Look for "
                "unbounded allocations, process spawning or output in the code and tests."
            ]
        tests = self.tests
        if tests is None:
            return [self.message] if self.message else []
        problems = [
            f"{failure.nodeid} [{failure.phase}] {failure.message}\n{failure.details}".rstrip()
            for failure in tests.failures[:max_failures]
        ]
        if len(tests.failures) > max_failures:
            problems.append(f"... and {len(tests.failures) - max_failures} more failure(s)")
        if not problems and self.message:
            problems.append(self.message)
        return problems

    def brief(self) -> dict[str, object]:
        """The deterministic part of the result, for prompts (no durations or output)."""
        tests = self.tests
        return {
            "status": self.status.value,
            "limit": self.limit,
            "tests": None
            if tests is None
            else {
                "collected": tests.collected,
                "passed": tests.passed,
                "failed": tests.failed,
                "errors": tests.errors,
                "skipped": tests.skipped,
                "failures": [f"{f.nodeid} [{f.phase}] {f.message}" for f in tests.failures[:10]],
            },
            "lint": None if self.lint is None else self.lint.status,
            "typecheck": None if self.typecheck is None else self.typecheck.status,
        }

    def describe(self) -> str:
        """One line for the activity log."""
        tests = self.tests
        counts = (
            f"{tests.passed} passed, {tests.failed} failed, {tests.errors} error(s)"
            if tests is not None
            else "no test results"
        )
        match self.status:
            case SandboxStatus.PASSED | SandboxStatus.FAILED:
                text = f"Tests {self.status.value} in the sandbox: {counts}"
            case SandboxStatus.NO_TESTS:
                text = "The sandbox collected no tests"
            case SandboxStatus.TIMEOUT:
                text = f"Test run timed out ({self.message})"
            case SandboxStatus.RESOURCE_LIMIT:
                text = f"Test run hit the {self.limit} limit ({self.message})"
            case SandboxStatus.ERROR:
                text = f"Sandbox error: {self.message}"
        checks = [
            f"{name} {check.status}"
            for name, check in (("lint", self.lint), ("types", self.typecheck))
            if check is not None
        ]
        return f"{text}" + (f"; {', '.join(checks)}" if checks else "")


@dataclass(frozen=True, slots=True)
class SandboxLimits:
    """Per-run limits. Set by the operator (settings), never by the caller of a run."""

    timeout_s: float = 120.0
    memory_mb: int = 768
    cpus: float = 1.0
    pids: int = 128
    workspace_mb: int = 64
    tmp_mb: int = 64
    max_archive_bytes: int = 8_000_000
    max_output_bytes: int = 1_000_000
    typecheck: bool = True
