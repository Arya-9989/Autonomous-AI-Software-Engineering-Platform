"""Agent pipeline behaviour, independent of HTTP and persistence."""

from __future__ import annotations

import asyncio
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from autoswe.llm.base import LLMRequest
from autoswe.llm.offline import OfflineProvider
from autoswe.pipeline.agents import PipelineState, default_agents
from autoswe.pipeline.artifacts import FileBundle, GeneratedFile, RepairRequest, StageName
from tests.conftest import LIBRARY_SPEC
from tests.fakes import ScriptedProvider


async def run_pipeline(provider: Any, spec: str = LIBRARY_SPEC, **state: Any) -> PipelineState:
    pipeline_state = PipelineState(spec=spec, **state)
    messages: list[str] = []

    async def emit(message: str) -> None:
        messages.append(message)

    for agent in default_agents():
        await agent.run(pipeline_state, provider, emit)
    pipeline_state.messages = messages  # type: ignore[attr-defined]
    return pipeline_state


async def test_offline_pipeline_produces_a_complete_validated_project() -> None:
    state = await run_pipeline(OfflineProvider())
    assert state.requirements is not None
    assert state.architecture is not None
    assert state.validation is not None and state.validation.passed
    assert state.validation.issues == []
    assert state.review is not None and state.review.score == 100

    paths = set(state.files)
    expected = {
        "ARCHITECTURE.md", "docs/architecture.json", "pyproject.toml", "app/main.py",
        "app/routers/books.py", "tests/test_loans.py", "Dockerfile",
        ".github/workflows/ci.yml", "deploy/k8s/deployment.yaml", "deploy/terraform/main.tf",
    }  # fmt: skip
    assert expected <= paths
    assert state.file_origin["Dockerfile"] is StageName.DEVOPS
    assert state.file_origin["app/main.py"] is StageName.IMPLEMENT
    assert "```mermaid" in state.files["ARCHITECTURE.md"].content


@pytest.mark.slow
async def test_generated_project_passes_its_own_lint_and_tests(tmp_path: Path) -> None:
    """The strongest check we have: run the generated repository's own ruff + pytest."""
    state = await run_pipeline(
        OfflineProvider(),
        spec="Inventory service. Entities: Product(name, price:float, stock:int, active:bool), "
        "Shipment(carrier, shipped_at:datetime, delivered_on?:date, notes?)",
    )
    for file in state.files.values():
        target = tmp_path / file.path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(file.content, encoding="utf-8", newline="\n")

    def run(*args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-m", *args], cwd=tmp_path, capture_output=True, text=True, check=False
        )

    lint = await asyncio.to_thread(run, "ruff", "check", ".")
    assert lint.returncode == 0, lint.stdout + lint.stderr
    tests = await asyncio.to_thread(run, "pytest", "-q", "-p", "no:cacheprovider")
    assert tests.returncode == 0, tests.stdout + tests.stderr
    assert "passed" in tests.stdout


async def test_repair_round_fixes_files_that_fail_validation() -> None:
    def broken_coder(request: LLMRequest[Any]) -> FileBundle:
        files = [
            GeneratedFile(path="pyproject.toml", content='[project]\nname = "x"\n'),
            GeneratedFile(path="README.md", content="# x\n"),
            GeneratedFile(path="app/main.py", content="def app(:\n"),
            GeneratedFile(path="tests/test_main.py", content="def test_x():\n    pass\n"),
        ]
        return FileBundle(files=files)

    def repair(request: LLMRequest[Any]) -> FileBundle:
        repair_request = RepairRequest.model_validate(request.context["repair"])
        assert [f.path for f in repair_request.files] == ["app/main.py"]
        assert any("python-syntax" in problem for problem in repair_request.problems)
        return FileBundle(files=[GeneratedFile(path="app/main.py", content="app = None\n")])

    provider = ScriptedProvider({"coder": broken_coder, "coder.repair": repair})
    state = await run_pipeline(provider)

    assert provider.calls.count("coder.repair") == 1
    assert state.files["app/main.py"].content == "app = None\n"
    assert state.validation is not None and state.validation.passed
    assert any("Repair round 1" in m for m in state.messages)  # type: ignore[attr-defined]


async def test_repair_that_changes_nothing_stops_early() -> None:
    def broken_coder(request: LLMRequest[Any]) -> FileBundle:
        return FileBundle(files=[GeneratedFile(path="app/main.py", content="def (:\n")])

    provider = ScriptedProvider({"coder": broken_coder})  # offline repair echoes files back
    state = await run_pipeline(provider, max_repair_rounds=3)
    assert provider.calls.count("coder.repair") == 1
    assert state.validation is not None and not state.validation.passed
    assert state.review is not None and state.review.score < 100


async def test_repair_is_skipped_when_disabled() -> None:
    def broken_coder(request: LLMRequest[Any]) -> FileBundle:
        return FileBundle(files=[GeneratedFile(path="app/main.py", content="def (:\n")])

    provider = ScriptedProvider({"coder": broken_coder})
    await run_pipeline(provider, max_repair_rounds=0)
    assert "coder.repair" not in provider.calls


async def test_stage_ownership_is_enforced() -> None:
    def greedy_coder(request: LLMRequest[Any]) -> FileBundle:
        return FileBundle(
            files=[
                GeneratedFile(path="app/main.py", content="x = 1\n"),
                GeneratedFile(path="Dockerfile", content="FROM scratch\n"),
                GeneratedFile(path="ARCHITECTURE.md", content="hijacked"),
            ]
        )

    def greedy_devops(request: LLMRequest[Any]) -> FileBundle:
        return FileBundle(
            files=[
                GeneratedFile(path="app/main.py", content="overwritten = True\n"),
                GeneratedFile(path="deploy/k8s/service.yaml", content="kind: Service\n"),
            ]
        )

    provider = ScriptedProvider({"coder": greedy_coder, "devops": greedy_devops})
    state = await run_pipeline(provider)
    assert state.files["app/main.py"].content == "x = 1\n"
    assert "Dockerfile" not in state.files
    assert state.files["ARCHITECTURE.md"].content.startswith("# ")
    assert "deploy/k8s/service.yaml" in state.files


async def test_unsafe_llm_paths_never_reach_the_project() -> None:
    def evil_coder(request: LLMRequest[Any]) -> FileBundle:
        return FileBundle(
            files=[
                GeneratedFile(path="../../outside.py", content="x = 1\n"),
                GeneratedFile(path="/etc/cron.d/job", content="* * * * * root id\n"),
            ]
        )

    state = await run_pipeline(ScriptedProvider({"coder": evil_coder}))
    assert not any(".." in p or p.startswith("/") for p in state.files)
    assert state.validation is not None
    assert sum(i.rule == "path-safety" for i in state.validation.issues) == 2
