"""Sandbox backends, the in-sandbox entry script and the result model.

The Docker backend is driven by ``tests/fake_docker.py`` here, so its control flow (hard
timeout, OOM detection, output caps, cleanup) is tested on every machine. Real containers are
exercised in ``test_sandbox_docker.py``.
"""

from __future__ import annotations

import io
import json
import os
import sys
import tarfile
from pathlib import Path
from typing import Any

import pytest

from autoswe.config import Settings
from autoswe.pipeline.artifacts import GeneratedFile
from autoswe.sandbox.backends import (
    DockerSandbox,
    LocalProcessSandbox,
    RemoteSandbox,
    parse_entry_output,
    workspace_archive,
)
from autoswe.sandbox.factory import build_sandbox
from autoswe.sandbox.inside import entry
from autoswe.sandbox.models import (
    SandboxLimits,
    SandboxResult,
    SandboxStatus,
    TestFailure,
    TestSummary,
)
from tests import sandbox_projects as projects

FAKE_DOCKER = [sys.executable, str(Path(__file__).with_name("fake_docker.py"))]


@pytest.fixture
def fake_docker(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    log = tmp_path / "docker.log"
    monkeypatch.setenv("FAKE_DOCKER_LOG", str(log))

    def make(mode: str, **limits: Any) -> tuple[DockerSandbox, Path]:
        monkeypatch.setenv("FAKE_DOCKER_MODE", mode)
        sandbox = DockerSandbox(
            "autoswe-sandbox:local",
            SandboxLimits(**limits),
            docker=FAKE_DOCKER,
            startup_grace_s=0.5,
        )
        return sandbox, log

    return make


def _calls(log: Path) -> list[list[str]]:
    return [json.loads(line) for line in log.read_text().splitlines()]


# --- docker run arguments: the security contract -----------------------------------------------


def test_docker_run_args_enforce_the_isolation_contract() -> None:
    sandbox = DockerSandbox("img:1", SandboxLimits(memory_mb=512, cpus=0.5, pids=64))
    args = sandbox.run_args("c1")

    def value(flag: str) -> list[str]:
        return [args[i + 1] for i, a in enumerate(args) if a == flag]

    assert value("--network") == ["none"]
    assert "--read-only" in args
    assert value("--cap-drop") == ["ALL"]
    assert value("--security-opt") == ["no-new-privileges:true"]
    assert value("--user") == ["10002:10002"]
    assert value("--memory") == ["512m"] and value("--memory-swap") == ["512m"]  # no swap
    assert value("--cpus") == ["0.5"]
    assert value("--pids-limit") == ["64"]
    assert "--pull=never" in args  # never fetch an image at run time
    assert "--init" in args
    assert value("--log-driver") == ["none"]  # a flood cannot fill the daemon's disk
    tmpfs = value("--tmpfs")
    assert len(tmpfs) == 2 and all("nosuid" in t and "noexec" in t for t in tmpfs)
    # Nothing from the host is mounted: the workspace arrives as a tar on stdin.
    for forbidden in ("-v", "--volume", "--mount", "--privileged", "--device", "--cap-add"):
        assert forbidden not in args
    assert not any(a.startswith(("--volume=", "--mount=", "--privileged")) for a in args)
    assert args[args.index("img:1") + 1 :][:2] == ["python", "/opt/autoswe-sandbox/entry.py"]


def test_docker_runtime_and_typecheck_switches() -> None:
    sandbox = DockerSandbox("img", SandboxLimits(typecheck=False), runtime="runsc")
    args = sandbox.run_args("c")
    assert args[args.index("--runtime") + 1] == "runsc"
    assert args[-1] == "--no-typecheck"
    assert "runtime runsc" in sandbox.isolation


def test_workspace_archive_is_deterministic_and_drops_unsafe_paths() -> None:
    files = [
        GeneratedFile(path="b.py", content="b = 1\n"),
        GeneratedFile(path="a/x.py", content="x = 1\n"),
        GeneratedFile(path="../escape.py", content="boom"),
        GeneratedFile(path="/etc/passwd", content="boom"),
    ]
    first = workspace_archive(files)
    assert first == workspace_archive(list(reversed(files)))
    with tarfile.open(fileobj=io.BytesIO(first)) as archive:
        members = archive.getmembers()
    assert [m.name for m in members] == ["a/x.py", "b.py"]
    assert all(m.isfile() and m.mode == 0o644 and m.uid == 0 for m in members)


def test_parse_entry_output_takes_the_last_result_line() -> None:
    out = (
        'noise\nAUTOSWE_SANDBOX_RESULT {"status": "failed"}\n'
        'AUTOSWE_SANDBOX_RESULT {"status": "passed"}\n'
    )
    assert parse_entry_output(out) == {"status": "passed"}
    assert parse_entry_output("AUTOSWE_SANDBOX_RESULT {broken") is None
    assert parse_entry_output("AUTOSWE_SANDBOX_RESULT [1]") is None
    assert parse_entry_output("nothing here") is None


# --- docker backend control flow (fake CLI) -----------------------------------------------------


async def test_docker_backend_reports_the_entry_result_and_cleans_up(fake_docker: Any) -> None:
    sandbox, log = fake_docker("ok")
    result = await sandbox.run(projects.passing())
    assert result.status is SandboxStatus.PASSED
    assert result.backend == "docker" and "no network" in result.isolation
    calls = _calls(log)
    name = calls[0][calls[0].index("--name") + 1]
    assert name.startswith("autoswe-sbx-")
    assert ["rm", "-f", name] in calls  # the container is always removed


async def test_docker_backend_hard_timeout_kills_the_container(fake_docker: Any) -> None:
    sandbox, log = fake_docker("hang", timeout_s=1.0)
    result = await sandbox.run(projects.passing())
    assert result.status is SandboxStatus.TIMEOUT
    assert result.repairable
    calls = _calls(log)
    name = calls[0][calls[0].index("--name") + 1]
    assert ["kill", name] in calls and ["rm", "-f", name] in calls


async def test_docker_backend_detects_oom_kills(fake_docker: Any) -> None:
    sandbox, _ = fake_docker("oom")
    result = await sandbox.run(projects.passing())
    assert result.status is SandboxStatus.RESOURCE_LIMIT
    assert result.limit == "memory"


async def test_docker_backend_sigkill_without_report_is_a_memory_limit(fake_docker: Any) -> None:
    sandbox, _ = fake_docker("sigkill")
    result = await sandbox.run(projects.passing())
    assert (result.status, result.limit) == (SandboxStatus.RESOURCE_LIMIT, "memory")


async def test_docker_backend_caps_output(fake_docker: Any) -> None:
    sandbox, _ = fake_docker("flood", max_output_bytes=100_000)
    result = await sandbox.run(projects.passing())
    assert (result.status, result.limit) == (SandboxStatus.RESOURCE_LIMIT, "output")


async def test_docker_backend_unreadable_report_is_an_error(fake_docker: Any) -> None:
    sandbox, _ = fake_docker("garbage")
    result = await sandbox.run(projects.passing())
    assert result.status is SandboxStatus.ERROR
    assert not result.repairable


async def test_docker_backend_missing_image(fake_docker: Any) -> None:
    sandbox, _ = fake_docker("no-image")
    result = await sandbox.run(projects.passing())
    assert result.status is SandboxStatus.ERROR
    assert "Unable to find image" in result.message
    ready, detail = await sandbox.ready()
    assert not ready and "not available" in detail


async def test_docker_backend_rejects_oversized_workspaces(fake_docker: Any) -> None:
    sandbox, log = fake_docker("ok", max_archive_bytes=1_000)
    big = [GeneratedFile(path="big.py", content="x = 1\n" * 1_000)]
    result = await sandbox.run(big)
    assert (result.status, result.limit) == (SandboxStatus.RESOURCE_LIMIT, "archive")
    assert not log.exists()  # docker was never called


async def test_docker_backend_without_cli(tmp_path: Path) -> None:
    sandbox = DockerSandbox("img", SandboxLimits(), docker=str(tmp_path / "no-such-docker"))
    result = await sandbox.run(projects.passing())
    assert result.status is SandboxStatus.ERROR
    assert "cannot start the docker CLI" in result.message


async def test_leftover_containers_of_the_pool_are_removed(fake_docker: Any) -> None:
    sandbox, log = fake_docker("ok")
    assert await sandbox.remove_leftovers() == 2
    calls = _calls(log)
    assert calls[0][:3] == ["ps", "-aq", "--filter"]
    assert calls[0][3] == "label=autoswe.sandbox.pool=default"
    assert calls[1] == ["rm", "-f", "leftover1", "leftover2"]
    assert (await sandbox.ready()) == (True, "ok")


# --- the in-sandbox entry script ----------------------------------------------------------------


def _tar(members: list[tarfile.TarInfo], payloads: dict[str, bytes] | None = None) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as archive:
        for member in members:
            data = (payloads or {}).get(member.name)
            archive.addfile(member, io.BytesIO(data) if data is not None else None)
    return buffer.getvalue()


def _run_entry(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], workspace: Path,
    data: bytes, *extra: str,
) -> dict[str, Any]:  # fmt: skip
    class _Stdin:
        buffer = io.BytesIO(data)

    monkeypatch.setattr(sys, "stdin", _Stdin)
    entry.main(["--workspace", str(workspace), "--stdin-tar", "--no-typecheck", *extra])
    result = parse_entry_output(capsys.readouterr().out)
    assert result is not None
    return result


def test_entry_refuses_archives_that_escape_the_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    member = tarfile.TarInfo("../outside.py")
    member.size = 1
    workspace = tmp_path / "ws"
    workspace.mkdir()
    result = _run_entry(monkeypatch, capsys, workspace, _tar([member], {member.name: b"x"}))
    assert result["status"] == "error"
    assert "could not unpack" in result["message"]
    assert not (tmp_path / "outside.py").exists()


def test_entry_keeps_absolute_member_paths_inside_the_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    member = tarfile.TarInfo("/etc/evil")  # the data filter strips the leading slash
    member.size = 1
    workspace = tmp_path / "ws"
    workspace.mkdir()
    _run_entry(monkeypatch, capsys, workspace, _tar([member], {member.name: b"x"}))
    assert (workspace / "etc" / "evil").read_bytes() == b"x"


def test_entry_refuses_links_and_devices(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    link = tarfile.TarInfo("app/secrets")
    link.type, link.linkname = tarfile.SYMTYPE, "/etc/shadow"
    result = _run_entry(monkeypatch, capsys, tmp_path, _tar([link]))
    assert result["status"] == "error"
    assert "unsupported archive member" in result["message"]


def test_entry_caps_the_archive_size(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    result = _run_entry(monkeypatch, capsys, tmp_path, b"x" * 2_000, "--max-archive-bytes", "100")
    assert (result["status"], result["limit"]) == ("resource_limit", "archive")


def test_entry_scrubs_the_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-should-never-leak")
    env = entry._environment(tmp_path, tmp_path, tmp_path / "r.json")
    assert "ANTHROPIC_API_KEY" not in env
    assert env["HOME"] == str(tmp_path)
    assert all("KEY" not in key and "TOKEN" not in key for key in env)


def test_entry_parses_ruff_and_mypy_output(tmp_path: Path) -> None:
    entry._WORKSPACE = tmp_path
    ruff = [{"filename": str(tmp_path / "app" / "x.py"), "location": {"row": 3},
             "code": "F401", "message": "unused import"}]  # fmt: skip
    step = {"exit_code": 1, "timed_out": False, "output": json.dumps(ruff), "duration_s": 0.1}
    lint = entry._lint(step)
    assert lint["status"] == "failed" and lint["issues"] == ["app/x.py:3: F401 unused import"]
    clean = entry._lint({**step, "exit_code": 0, "output": "[]"})
    assert clean["status"] == "passed"
    crashed = entry._lint({**step, "exit_code": 2, "output": "No module named ruff"})
    assert crashed["status"] == "error" and crashed["issues"] == ["No module named ruff"]
    mypy_out = "app/x.py:1: error: Incompatible types  [assignment]\nnote: hi"
    typed = entry._typecheck({**step, "output": mypy_out})
    assert typed["status"] == "failed" and typed["issue_count"] == 1
    assert entry._check({**step, "timed_out": True}, 0, [])["status"] == "timeout"


# --- local backend: really runs pytest ----------------------------------------------------------


LOCAL = LocalProcessSandbox(SandboxLimits(timeout_s=60, typecheck=False))


async def test_local_backend_runs_a_passing_suite() -> None:
    result = await LOCAL.run(projects.passing())
    assert result.status is SandboxStatus.PASSED, result
    assert result.tests is not None and result.tests.passed == 2
    assert result.lint is not None and result.lint.status == "passed"
    assert result.isolation.startswith("NONE")  # labelled for what it is


async def test_local_backend_reports_structured_failures() -> None:
    result = await LOCAL.run(projects.failing())
    assert result.status is SandboxStatus.FAILED
    assert result.tests is not None and result.tests.failed == 2
    failure = result.tests.failures[0]
    assert failure.nodeid == "tests/test_calc.py::test_add"
    assert failure.phase == "call"
    assert "assert -1 == 5" in failure.message
    assert "tests/test_calc.py" in failure.details
    assert str(Path.cwd()) not in failure.details  # paths are workspace-relative


async def test_local_backend_reports_collection_errors() -> None:
    files = projects.project("from app.missing import nope\n\n\ndef test_x() -> None:\n    pass\n")
    result = await LOCAL.run(files)
    assert result.status is SandboxStatus.FAILED
    assert result.tests is not None and result.tests.errors == 1
    assert result.tests.failures[0].phase == "collect"
    assert "ModuleNotFoundError" in result.tests.failures[0].message


async def test_local_backend_no_tests() -> None:
    result = await LOCAL.run(projects.project("x = 1\n"))
    assert result.status is SandboxStatus.NO_TESTS
    assert not result.repairable


async def test_local_backend_enforces_the_wall_clock_limit() -> None:
    sandbox = LocalProcessSandbox(SandboxLimits(timeout_s=6, typecheck=False))
    result = await sandbox.run(projects.project(projects.INFINITE_LOOP))
    assert result.status is SandboxStatus.TIMEOUT
    assert result.duration_s < 20


async def test_local_backend_runs_mypy_when_enabled() -> None:
    sandbox = LocalProcessSandbox(SandboxLimits(timeout_s=90, typecheck=True))
    wrong = "def add(a: int, b: int) -> int:\n    return str(a + b)\n"
    files = projects.project(projects.CALC_TESTS, calc=wrong)
    result = await sandbox.run(files)
    assert result.typecheck is not None
    assert result.typecheck.status == "failed"
    assert any("Incompatible return value" in issue for issue in result.typecheck.issues)


async def test_local_backend_hides_host_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTOSWE_TEST_SECRET_TOKEN", "do-not-leak")
    result = await LOCAL.run(projects.project(projects.ESCAPES))
    assert result.tests is not None
    leak = next(f for f in result.tests.failures if "secrets_leak" in f.nodeid)
    assert "AUTOSWE_TEST_SECRET_TOKEN" not in leak.message + leak.details


# --- model helpers ------------------------------------------------------------------------------


def test_result_problems_and_descriptions() -> None:
    failures = [TestFailure(nodeid=f"t::test_{i}", phase="call", message="boom") for i in range(12)]
    failed = SandboxResult(
        status=SandboxStatus.FAILED, backend="x", isolation="y",
        tests=TestSummary(collected=12, failed=12, failures=failures),
    )  # fmt: skip
    problems = failed.problems(max_failures=10)
    assert len(problems) == 11 and problems[-1] == "... and 2 more failure(s)"
    assert "12 failed" in failed.describe()
    timeout = SandboxResult(status=SandboxStatus.TIMEOUT, backend="x", isolation="y", message="5 s")
    assert "infinite loops" in timeout.problems()[0]
    limit = SandboxResult(status=SandboxStatus.RESOURCE_LIMIT, backend="x", isolation="y",
                          limit="pids", message="m")  # fmt: skip
    assert "pids limit" in limit.problems()[0] and "pids limit" in limit.describe()
    error = SandboxResult(status=SandboxStatus.ERROR, backend="x", isolation="y", message="down")
    assert error.problems() == ["down"] and "Sandbox error" in error.describe()
    assert SandboxResult(status=SandboxStatus.NO_TESTS, backend="x", isolation="y").brief() == {
        "status": "no_tests", "limit": None, "tests": None, "lint": None, "typecheck": None,
    }  # fmt: skip


def test_factory_builds_each_backend(tmp_path: Path) -> None:
    base: dict[str, Any] = {"_env_file": None, "auth_disabled": True}
    assert build_sandbox(Settings(**base)) is None
    assert isinstance(build_sandbox(Settings(**base, sandbox_backend="local")), LocalProcessSandbox)
    docker = build_sandbox(Settings(**base, sandbox_backend="docker", sandbox_image="x:1"))
    assert isinstance(docker, DockerSandbox) and docker.image == "x:1"
    remote = build_sandbox(
        Settings(**base, sandbox_backend="remote", sandbox_runner_url="http://r:8080/",
                 sandbox_runner_token="t" * 20)
    )  # fmt: skip
    assert isinstance(remote, RemoteSandbox) and remote.url == "http://r:8080"


@pytest.mark.parametrize(
    "overrides",
    [
        {"sandbox_backend": "remote"},
        {"sandbox_backend": "remote", "sandbox_runner_url": "http://r"},
        {"sandbox_runner_token": "short"},
        {"sandbox_pool": "Bad Pool!"},
        {"sandbox_memory_mb": 16},
    ],
)
def test_sandbox_settings_are_validated(overrides: dict[str, Any]) -> None:
    with pytest.raises(ValueError):  # noqa: PT011 - pydantic wraps each case differently
        Settings(_env_file=None, auth_disabled=True, **overrides)


def test_blank_sandbox_values_count_as_unset() -> None:
    settings = Settings(
        _env_file=None, auth_disabled=True, sandbox_docker_runtime=" ", sandbox_runner_url=""
    )
    assert settings.sandbox_docker_runtime is None and settings.sandbox_runner_url is None


@pytest.mark.skipif(os.name != "posix", reason="POSIX rlimits")
async def test_local_backend_memory_rlimit_on_posix() -> None:
    sandbox = LocalProcessSandbox(SandboxLimits(timeout_s=60, memory_mb=256, typecheck=False))
    result = await sandbox.run(projects.project(projects.MEMORY_HOG))
    assert result.status is SandboxStatus.FAILED
    assert result.tests is not None
    assert "MemoryError" in result.tests.failures[0].message


# --- the entry script run in-process on a real workspace -----------------------------------------


def _write(workspace: Path, files: list[GeneratedFile]) -> None:
    for file in files:
        target = workspace / file.path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(file.content, encoding="utf-8", newline="\n")


def _entry(workspace: Path, capsys: pytest.CaptureFixture[str], *extra: str) -> dict[str, Any]:
    entry.main(["--workspace", str(workspace), *extra])
    result = parse_entry_output(capsys.readouterr().out)
    assert result is not None
    return result


def test_entry_runs_lint_tests_and_types_and_reports_a_verdict(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _write(tmp_path, projects.passing())
    result = _entry(tmp_path, capsys, "--deadline-s", "90")
    assert result["status"] == "passed"
    assert result["lint"]["status"] == "passed"
    assert result["typecheck"]["status"] == "passed"
    assert result["tests"]["collected"] == 2 and result["tests"]["passed"] == 2
    assert result["tests"]["exit_code"] == 0


def test_entry_reports_failures_and_missing_tests(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    failing = tmp_path / "failing"
    _write(failing, projects.failing())
    result = _entry(failing, capsys, "--no-typecheck")
    assert result["status"] == "failed" and result["tests"]["failed"] == 2
    assert result["message"] == "pytest exited with 1"
    empty = tmp_path / "empty"
    _write(empty, projects.project("x = 1\n"))
    assert _entry(empty, capsys, "--no-typecheck")["status"] == "no_tests"


def test_entry_kills_a_step_that_outlives_the_deadline(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _write(tmp_path, projects.project(projects.INFINITE_LOOP))
    result = _entry(tmp_path, capsys, "--deadline-s", "6")
    assert result["status"] == "timeout"
    assert result["tests"]["exit_code"] is None
    assert "typecheck" not in result  # no time left for further steps


def test_entry_reads_cgroup_counters_where_they_exist(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    counters = iter([{"oom_kill": 0, "pids_max": 0}, {"oom_kill": 1, "pids_max": 0}])
    monkeypatch.setattr(entry, "_cgroup_events", lambda: next(counters))
    _write(tmp_path, projects.passing())
    result = _entry(tmp_path, capsys, "--no-typecheck", "--cgroup-events")
    assert (result["status"], result["limit"]) == ("resource_limit", "memory")
    pids = iter([{"pids_max": 0}, {"pids_max": 3}])
    monkeypatch.setattr(entry, "_cgroup_events", lambda: next(pids))
    result = _entry(tmp_path, capsys, "--no-typecheck", "--cgroup-events")
    assert (result["status"], result["limit"]) == ("resource_limit", "pids")
    monkeypatch.undo()
    assert isinstance(entry._cgroup_events(), dict)  # absent files on non-Linux hosts: {}


async def test_runner_with_docker_reports_readiness_and_cleans_up_at_start(
    fake_docker: Any,
) -> None:
    import httpx2

    from autoswe.sandbox.service import RunnerSettings, create_runner_app

    settings = RunnerSettings(_env_file=None, sandbox_runner_token="t" * 20,
                              log_format="console", log_level="WARNING")  # fmt: skip
    sandbox, log = fake_docker("ok")
    app = create_runner_app(settings, sandbox=sandbox)
    async with app.router.lifespan_context(app):  # removes the pool's leftover containers
        assert ["rm", "-f", "leftover1", "leftover2"] in _calls(log)
        transport = httpx2.ASGITransport(app=app)
        async with httpx2.AsyncClient(transport=transport, base_url="http://r") as client:
            assert (await client.get("/readyz")).json() == {"status": "ok", "backend": "docker"}
            os.environ["FAKE_DOCKER_MODE"] = "no-image"
            not_ready = await client.get("/readyz")
    assert not_ready.status_code == 503
    assert "not available" in not_ready.json()["detail"]
