"""The entry script's TypeScript and Go paths, run in-process (parsers and both stack checks).

In containers these paths run inside the sandbox images (``test_sandbox_docker.py``); here
their parsing and verdicts are pinned against real tool output shapes, and the checks run on this
machine with whatever toolchains it has, reporting a missing one as a sandbox error.
"""

# ruff: noqa: E501 - tool output samples (ESLint, JUnit, go test -json) are kept verbatim

from __future__ import annotations

import json
import shutil
import time
from pathlib import Path
from typing import Any

import pytest

from autoswe.llm.offline import OfflineProvider
from autoswe.pipeline.agents import PipelineState, run_pipeline
from autoswe.sandbox.inside import entry
from autoswe.stacks import Stack

INVENTORY = "Inventory service. Entities: Product(name, price:float, stock:int, active:bool)"


def _step(output: str, exit_code: int | None = 1, *, timed_out: bool = False) -> dict[str, Any]:
    return {"exit_code": exit_code, "timed_out": timed_out, "output": output, "duration_s": 0.1}


@pytest.fixture(autouse=True)
def workspace(tmp_path: Path) -> Path:
    entry._WORKSPACE = tmp_path
    return tmp_path


def test_eslint_json_is_parsed(workspace: Path) -> None:
    results = [{"filePath": str(workspace / "src" / "a.ts"), "messages": [
        {"ruleId": "no-unused-vars", "line": 3, "message": "'x' is unused", "severity": 2},
        {"ruleId": None, "line": 1, "message": "Parsing error", "severity": 2},
    ]}]  # fmt: skip
    lint = entry._eslint(_step("warning: noise\n" + json.dumps(results)))
    assert lint["status"] == "failed" and lint["issue_count"] == 2
    assert lint["issues"] == [
        "src/a.ts:3: no-unused-vars 'x' is unused",
        "src/a.ts:1: error Parsing error",
    ]
    assert entry._eslint(_step("[]", 0))["status"] == "passed"
    assert entry._eslint(_step("Oops! Something went wrong!", 2))["status"] == "error"


def test_tsc_output_is_parsed() -> None:
    out = "src/a.ts(3,7): error TS2322: Type 'string' is not assignable to type 'number'.\n"
    typed = entry._tsc(_step(out))
    assert typed["status"] == "failed" and typed["issues"] == [out.strip()]


def test_node_junit_report_is_parsed(workspace: Path) -> None:
    report = workspace / "junit.xml"
    report.write_text(f"""<testsuites>
  <testsuite name="/books">
    <testcase name="creates" file="{workspace / 'test' / 'books.test.ts'}"/>
    <testcase name="updates" file="{workspace / 'test' / 'books.test.ts'}">
      <failure message="Expected values to be strictly equal:&#10;&#10;201 !== 299">at {workspace / 'test' / 'books.test.ts'}:12:5</failure>
    </testcase>
    <testcase name="later"><skipped/></testcase>
  </testsuite>
  <testcase name="reports healthy"/>
</testsuites>""", encoding="utf-8")  # fmt: skip
    parsed = entry._junit(report)
    assert (parsed["collected"], parsed["passed"], parsed["failed"], parsed["skipped"]) == (
        4,
        2,
        1,
        1,
    )
    failure = parsed["failures"][0]
    assert failure["nodeid"] == "test/books.test.ts::/books > updates"
    assert failure["message"] == "Expected values to be strictly equal:  201 !== 299"
    assert failure["details"] == "at test/books.test.ts:12:5"
    report.write_text("<not xml", encoding="utf-8")
    assert entry._junit(report)["collected"] == 0
    assert entry._junit(workspace / "missing.xml")["collected"] == 0


def _events(*events: dict[str, Any]) -> str:
    return "\n".join(json.dumps(event) for event in events) + "\nnot json\n{broken\n"


def test_go_test_json_counts_and_failures() -> None:
    out = _events(
        {"Action": "run", "Package": "p", "Test": "TestA"},
        {"Action": "output", "Package": "p", "Test": "TestA", "Output": "=== RUN   TestA\n"},
        {"Action": "output", "Package": "p", "Test": "TestA", "Output": "    a_test.go:9: want 200\n"},
        {"Action": "fail", "Package": "p", "Test": "TestA"},
        {"Action": "pass", "Package": "p", "Test": "TestB"},
        {"Action": "skip", "Package": "p", "Test": "TestC"},
        {"Action": "fail", "Package": "p"},
    )  # fmt: skip
    parsed = entry._go_test_events(out)
    assert (parsed["collected"], parsed["passed"], parsed["failed"], parsed["skipped"]) == (
        3,
        1,
        1,
        1,
    )
    assert parsed["failures"][0] == {"nodeid": "p::TestA", "phase": "call",
                                     "message": "a_test.go:9: want 200",
                                     "details": "=== RUN   TestA\n    a_test.go:9: want 200\n"}  # fmt: skip
    assert "a_test.go:9" in entry._go_text(out) and "not json" in entry._go_text(out)


def test_go_build_failures_are_collection_errors() -> None:
    out = _events(
        {"Action": "build-output", "ImportPath": "p [p.test]", "Output": "# p\n"},
        {"Action": "build-output", "ImportPath": "p [p.test]", "Output": "./s.go:3:1: syntax error\n"},
        {"Action": "fail", "Package": "p", "FailedBuild": "p [p.test]"},
    )  # fmt: skip
    parsed = entry._go_test_events(out)
    assert parsed["errors"] == 1
    assert parsed["failures"][0]["phase"] == "collect"
    assert parsed["failures"][0]["message"] == "./s.go:3:1: syntax error"


def test_gofmt_and_vet_verdicts() -> None:
    assert entry._gofmt(_step("", 0))["status"] == "passed"
    unformatted = entry._gofmt(_step("store.go\nmain.go\n", 0))
    assert (
        unformatted["status"] == "failed"
        and unformatted["issues"][0] == "store.go: not gofmt-formatted"
    )
    assert entry._gofmt(_step("cannot parse", 2))["status"] == "error"
    vet = entry._go_vet(_step("# p\nvet: ./a.go:3:1: bad\n"))
    assert vet["status"] == "failed" and vet["issue_count"] == 1


def test_verdicts() -> None:
    assert entry._verdict(_step("", 0), {"collected": 2}, "t") == {"status": "passed"}
    assert entry._verdict(_step("", 0), {"collected": 0}, "t")["status"] == "no_tests"
    assert entry._verdict(_step("", 1), {"collected": 2, "failed": 1}, "t")["status"] == "failed"
    assert entry._verdict(_step("", None, timed_out=True), {}, "t")["status"] == "timeout"
    missing = {**_step("go: No such file", None), "missing": True}
    assert entry._verdict(missing, {}, "go test") == {
        "status": "error", "message": "go test is not available: go: No such file",
    }  # fmt: skip


def test_a_missing_tool_is_reported_not_raised(workspace: Path) -> None:
    step = entry._run(["definitely-not-a-real-tool-4242"], workspace, {}, 5)
    assert step["missing"] is True and step["exit_code"] is None


def test_go_environment_is_offline_and_seeds_the_cache(
    workspace: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seed = tmp_path / "seed"
    (seed / "aa").mkdir(parents=True)
    (seed / "aa" / "entry").write_text("compiled", encoding="utf-8")
    monkeypatch.setattr(entry, "GO_CACHE_SEED", seed)
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    env = entry._go_environment(workspace, scratch)
    assert env["GOPROXY"] == "off" and env["GOTOOLCHAIN"] == "local" and env["CGO_ENABLED"] == "0"
    assert (Path(env["GOCACHE"]) / "aa" / "entry").read_text(encoding="utf-8") == "compiled"


def test_node_modules_are_linked_once(
    workspace: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    modules = tmp_path / "pinned"
    modules.mkdir()
    monkeypatch.setattr(entry, "NODE_MODULES", modules)
    entry._link_node_modules(workspace)
    link = workspace / "node_modules"
    if link.exists():  # symlinks may need privileges on Windows; then nothing is linked
        assert link.resolve() == modules.resolve()
        entry._link_node_modules(workspace)  # idempotent


async def _write_project(stack: Stack, root: Path) -> None:
    state = PipelineState(spec=INVENTORY, stack=stack)

    async def quiet(_: str) -> None:
        return None

    await run_pipeline(state, OfflineProvider(), quiet)
    for file in state.files.values():
        target = root / file.path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(file.content, encoding="utf-8", newline="\n")


@pytest.mark.skipif(shutil.which("node") is None, reason="needs Node.js 24")
async def test_typescript_check_runs_the_tests_with_node(
    workspace: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _write_project(Stack.TYPESCRIPT, workspace)
    monkeypatch.setattr(entry, "NODE_MODULES", tmp_path / "no-tools")  # linters not installed
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    deadline = time.monotonic() + 90
    result = entry._check_typescript(workspace, scratch, lambda: deadline - time.monotonic(), True)
    assert result["status"] == "passed", result
    assert result["tests"]["collected"] == result["tests"]["passed"] == 7
    assert result["lint"]["status"] == "error"  # reported, not mistaken for a verdict
    assert result["typecheck"]["status"] == "error"


async def test_go_check_without_a_toolchain_is_a_sandbox_error(
    workspace: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    if shutil.which("go"):
        pytest.skip("a Go toolchain is installed; this case is about its absence")
    await _write_project(Stack.GO, workspace)
    monkeypatch.setattr(entry, "GO_CACHE_SEED", tmp_path / "no-seed")
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    deadline = time.monotonic() + 30
    result = entry._check_go(workspace, scratch, lambda: deadline - time.monotonic(), True)
    assert result["status"] == "error" and "not available" in result["message"]
    assert result["lint"]["status"] == "error"
