"""The reviewer's sandboxed test-and-repair loop, from scripted outcomes to real test runs."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

import pytest

from autoswe.llm.base import LLMRequest
from autoswe.llm.offline import OfflineProvider
from autoswe.pipeline.agents import PipelineState, default_agents
from autoswe.pipeline.artifacts import FileBundle, GeneratedFile, RepairRequest, StageName
from autoswe.sandbox.backends import LocalProcessSandbox
from autoswe.sandbox.models import (
    SandboxLimits,
    SandboxResult,
    SandboxStatus,
    TestFailure,
    TestSummary,
)
from autoswe.stacks import Stack
from tests import sandbox_projects as projects
from tests.conftest import ALICE, BOB, HarnessFactory
from tests.fakes import ScriptedProvider


def _result(status: SandboxStatus, **extra: Any) -> SandboxResult:
    return SandboxResult(status=status, backend="scripted", isolation="test", **extra)


PASSED = _result(SandboxStatus.PASSED, tests=TestSummary(collected=3, passed=3))
FAILED = _result(
    SandboxStatus.FAILED,
    message="pytest exited with 1",
    tests=TestSummary(
        collected=3, passed=2, failed=1,
        failures=[TestFailure(
            nodeid="tests/test_books.py::test_create_and_get_book", phase="call",
            message="assert 500 == 201",
            details="app/routers/books.py:42: in create_book\nE   assert 500 == 201",
        )],
    ),
)  # fmt: skip


class ScriptedSandbox:
    name = "scripted"

    def __init__(self, *results: SandboxResult) -> None:
        self.results = list(results)
        self.seen: list[dict[str, str]] = []

    async def run(
        self, files: Sequence[GeneratedFile], stack: Stack = Stack.PYTHON
    ) -> SandboxResult:
        self.seen.append({f.path: f.content for f in files})
        return self.results.pop(0) if len(self.results) > 1 else self.results[0]


async def run_pipeline(provider: Any, sandbox: Any, rounds: int = 2) -> PipelineState:
    state = PipelineState(spec=projects_spec(), sandbox=sandbox, max_test_repair_rounds=rounds)
    messages: list[str] = []

    async def emit(message: str) -> None:
        messages.append(message)

    for agent in default_agents():
        result = await agent.run(state, provider, emit)
    state.messages = messages  # type: ignore[attr-defined]
    state.summary = result.summary  # type: ignore[attr-defined]
    return state


def projects_spec() -> str:
    return "A REST API for a small library where members borrow books."


def _repair_editing(path: str, marker: str) -> Callable[[LLMRequest[Any]], FileBundle]:
    def repair(request: LLMRequest[Any]) -> FileBundle:
        bundle = RepairRequest.model_validate(request.context["repair"])
        target = next(f for f in bundle.files if f.path == path)
        return FileBundle(files=[target.model_copy(update={"content": target.content + marker})])

    return repair


async def test_passing_tests_need_no_repair() -> None:
    sandbox = ScriptedSandbox(PASSED)
    provider = ScriptedProvider()
    state = await run_pipeline(provider, sandbox)
    assert "coder.repair" not in provider.calls
    assert len(sandbox.seen) == 1
    assert "app/main.py" in sandbox.seen[0] and "Dockerfile" in sandbox.seen[0]
    report = state.test_report()
    assert report is not None and report["repair_rounds"] == 0
    assert report["final"]["status"] == "passed"
    assert "tests 3/3 passed" in state.summary  # type: ignore[attr-defined]


async def test_failing_tests_are_repaired_then_pass() -> None:
    sandbox = ScriptedSandbox(FAILED, PASSED)
    requests: list[LLMRequest[Any]] = []
    marker = "\n# fixed by repair\n"
    edit = _repair_editing("app/routers/books.py", marker)

    def repair(request: LLMRequest[Any]) -> FileBundle:
        requests.append(request)
        return edit(request)

    provider = ScriptedProvider({"coder.repair": repair})
    state = await run_pipeline(provider, sandbox)

    assert provider.calls.count("coder.repair") == 1
    (request,) = requests
    assert request.metadata == {"repair.kind": "tests", "repair.round": 1}
    bundle = RepairRequest.model_validate(request.context["repair"])
    sent = {f.path for f in bundle.files}
    # The failing test's file and the file its traceback names are always included.
    assert {"tests/test_books.py", "app/routers/books.py"} <= sent
    assert "Dockerfile" not in sent
    assert bundle.problems[0].startswith("tests/test_books.py::test_create_and_get_book [call]")
    # The second run saw the repaired file.
    assert sandbox.seen[1]["app/routers/books.py"].endswith(marker)
    assert state.files["app/routers/books.py"].content.endswith(marker)
    report = state.test_report()
    assert report is not None
    assert [a["status"] for a in report["attempts"]] == ["failed", "passed"]
    assert report["repair_rounds"] == 1 and not report["repair_budget_exhausted"]
    assert any("Test repair round 1" in m for m in state.messages)  # type: ignore[attr-defined]


async def test_repair_budget_is_bounded() -> None:
    sandbox = ScriptedSandbox(FAILED)  # fails forever
    provider = ScriptedProvider({"coder.repair": _repair_editing("app/routers/books.py", "#")})
    state = await run_pipeline(provider, sandbox, rounds=2)
    assert provider.calls.count("coder.repair") == 2
    assert len(sandbox.seen) == 3
    report = state.test_report()
    assert report is not None and report["repair_budget_exhausted"]
    assert any("budget of 2 round(s) used up" in m for m in state.messages)  # type: ignore[attr-defined]
    assert state.review is not None and state.review.score <= 75  # offline review penalises it
    assert any(f.severity == "error" and "does not pass" in f.message
               for f in state.review.findings)  # fmt: skip


async def test_zero_rounds_reports_without_repairing() -> None:
    provider = ScriptedProvider()
    state = await run_pipeline(provider, ScriptedSandbox(FAILED), rounds=0)
    assert "coder.repair" not in provider.calls
    report = state.test_report()
    assert report is not None and not report["repair_budget_exhausted"]


@pytest.mark.parametrize(
    ("result", "hint"),
    [
        (_result(SandboxStatus.TIMEOUT, message="killed after 120 s"), "infinite loops"),
        (_result(SandboxStatus.RESOURCE_LIMIT, limit="memory", message="768 MiB"),
         "memory limit"),
    ],
)  # fmt: skip
async def test_timeouts_and_limits_are_fed_back_for_repair(
    result: SandboxResult, hint: str
) -> None:
    problems: list[str] = []

    def repair(request: LLMRequest[Any]) -> FileBundle:
        bundle = RepairRequest.model_validate(request.context["repair"])
        problems.extend(bundle.problems)
        assert any(f.path.startswith("app/") for f in bundle.files)  # sources are sent
        return _repair_editing("app/main.py", "\n")(request)

    provider = ScriptedProvider({"coder.repair": repair})
    state = await run_pipeline(provider, ScriptedSandbox(result, PASSED))
    assert hint in problems[0]
    assert state.test_result is not None and state.test_result.status is SandboxStatus.PASSED


async def test_sandbox_errors_are_not_blamed_on_the_code() -> None:
    error = _result(SandboxStatus.ERROR, message="cannot reach the sandbox runner")
    provider = ScriptedProvider()
    state = await run_pipeline(provider, ScriptedSandbox(error))
    assert "coder.repair" not in provider.calls
    assert state.review is not None
    assert any("could not be executed" in f.message for f in state.review.findings)


async def test_repair_that_changes_nothing_stops_the_loop() -> None:
    sandbox = ScriptedSandbox(FAILED)
    provider = ScriptedProvider()  # offline repair echoes the files back unchanged
    state = await run_pipeline(provider, sandbox, rounds=3)
    assert provider.calls.count("coder.repair") == 1
    assert len(sandbox.seen) == 1
    assert any("no usable changes" in m for m in state.messages)  # type: ignore[attr-defined]


async def test_test_repair_respects_stage_ownership() -> None:
    def greedy(request: LLMRequest[Any]) -> FileBundle:
        return FileBundle(
            files=[
                GeneratedFile(path="Dockerfile", content="FROM scratch\n"),
                GeneratedFile(path="ARCHITECTURE.md", content="hijacked\n"),
                GeneratedFile(path="../outside.py", content="x = 1\n"),
                GeneratedFile(path="app/helpers.py", content="HELP = 1\n"),
            ]
        )

    provider = ScriptedProvider({"coder.repair": greedy})
    state = await run_pipeline(provider, ScriptedSandbox(FAILED, PASSED))
    assert state.files["Dockerfile"].content != "FROM scratch\n"
    assert state.files["ARCHITECTURE.md"].content != "hijacked\n"
    assert "../outside.py" not in state.files
    assert state.files["app/helpers.py"].content == "HELP = 1\n"  # a new module is allowed
    assert state.file_origin["app/helpers.py"] is StageName.IMPLEMENT


async def test_real_failing_suite_is_repaired_in_the_local_sandbox() -> None:
    """Real pytest: a coder writes a bug, the sandbox catches it, the repair fixes it."""

    def buggy_coder(request: LLMRequest[Any]) -> FileBundle:
        return FileBundle(files=projects.failing())

    def fix(request: LLMRequest[Any]) -> FileBundle:
        bundle = RepairRequest.model_validate(request.context["repair"])
        assert any("assert -1 == 5" in problem for problem in bundle.problems)
        return FileBundle(files=[GeneratedFile(path="app/calc.py", content=projects.CALC_OK)])

    provider = ScriptedProvider({"coder": buggy_coder, "coder.repair": fix})
    sandbox = LocalProcessSandbox(SandboxLimits(timeout_s=60, typecheck=False))
    state = await run_pipeline(provider, sandbox)
    report = state.test_report()
    assert report is not None
    assert [a["status"] for a in report["attempts"]] == ["failed", "passed"]
    assert state.files["app/calc.py"].content == projects.CALC_OK


async def test_offline_pipeline_tests_pass_in_the_local_sandbox() -> None:
    sandbox = LocalProcessSandbox(SandboxLimits(timeout_s=90, typecheck=False))
    state = await run_pipeline(OfflineProvider(), sandbox)
    result = state.test_result
    assert result is not None
    assert result.status is SandboxStatus.PASSED, result.describe()
    assert result.tests is not None and result.tests.passed >= 10
    assert state.review is not None and state.review.score == 100
    assert "generated tests pass in the sandbox" in state.review.summary


async def test_jobs_api_exposes_the_test_report(harness_factory: HarnessFactory) -> None:
    harness = await harness_factory(sandbox_backend="local", sandbox_typecheck=False)
    job = await harness.submit()
    detail = await harness.wait_for(job["id"], {"succeeded", "failed"}, max_wait_s=120)
    assert detail["status"] == "succeeded", detail.get("error")
    assert detail["tests"]["status"] == "passed"
    assert detail["tests"]["backend"] == "local"
    assert detail["tests"]["passed"] == detail["tests"]["collected"] > 0
    assert detail["tests"]["attempts"] == 1

    report = (await harness.client.get(f"/api/v1/jobs/{job['id']}/tests", headers=ALICE)).json()
    assert report["final"]["status"] == "passed"
    assert report["final"]["isolation"].startswith("NONE")
    # Another principal cannot see it (404, not 403).
    other = await harness.client.get(f"/api/v1/jobs/{job['id']}/tests", headers=BOB)
    assert other.status_code == 404


async def test_test_report_is_absent_without_a_sandbox(harness_factory: HarnessFactory) -> None:
    harness = await harness_factory()
    job = await harness.submit()
    detail = await harness.wait_for(job["id"], {"succeeded"})
    assert detail["tests"] is None
    response = await harness.client.get(f"/api/v1/jobs/{job['id']}/tests", headers=ALICE)
    assert response.status_code == 404
    assert response.json()["detail"] == "Tests were not run for this job"
