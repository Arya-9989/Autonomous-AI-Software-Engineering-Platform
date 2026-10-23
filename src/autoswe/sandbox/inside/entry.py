"""Entry point that runs *inside* the sandbox (standard library only).

It unpacks the job's workspace, runs ruff, pytest and (optionally) mypy against it with a
shared wall-clock budget, and prints one JSON result line prefixed with ``RESULT_PREFIX``. It
never trusts the workspace: the archive is extracted with tarfile's ``data`` filter (no absolute
paths, ``..``, links that leave the tree, devices or setuid bits), every step runs in its own
process group with a scrubbed environment, and only the last bytes of each step's output are
kept, so a test that floods stdout cannot exhaust the sandbox's or the runner's memory.

The Docker backend runs this as an unprivileged user in a read-only, network-less container; the
local backend runs it as a plain subprocess (see ``autoswe.sandbox.backends``).
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import shutil
import signal
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any

RESULT_PREFIX = "AUTOSWE_SANDBOX_RESULT "
OUTPUT_TAIL_BYTES = 16_000
MAX_ISSUES = 20
PLUGIN_DIR = Path(__file__).resolve().parent


class _Tail:
    """Drains a pipe in a thread and keeps only the last ``limit`` bytes."""

    def __init__(self, stream: Any, limit: int = OUTPUT_TAIL_BYTES) -> None:
        self._chunks: deque[bytes] = deque()
        self._size = 0
        self._limit = limit
        self._thread = threading.Thread(target=self._drain, args=(stream,), daemon=True)
        self._thread.start()

    def _drain(self, stream: Any) -> None:
        for chunk in iter(lambda: stream.read(4096), b""):
            self._chunks.append(chunk)
            self._size += len(chunk)
            while self._size > self._limit and len(self._chunks) > 1:
                self._size -= len(self._chunks.popleft())

    def text(self) -> str:
        # A descendant that left the process group may still hold the pipe: do not wait for it.
        self._thread.join(timeout=1.0)
        data = b"".join(self._chunks)[-self._limit :]
        return data.decode("utf-8", "replace")


def _kill_tree(proc: subprocess.Popen[bytes]) -> None:
    if sys.platform != "win32":
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            proc.kill()
    else:  # Windows: terminate the whole tree, not just the direct child
        subprocess.run(  # noqa: S603 - fixed argv
            ["taskkill", "/F", "/T", "/PID", str(proc.pid)],  # noqa: S607
            capture_output=True,
            check=False,
        )


def _run(argv: list[str], cwd: Path, env: dict[str, str], timeout: float) -> dict[str, Any]:
    started = time.monotonic()
    kwargs: dict[str, Any] = {}
    if sys.platform != "win32":
        kwargs["start_new_session"] = True
    else:
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    try:
        proc = subprocess.Popen(  # noqa: S603 - argv is built by this script, not from input
            argv,
            cwd=cwd,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            **kwargs,
        )
    except OSError as exc:  # the toolchain is missing: a sandbox problem, not a code problem
        return {"exit_code": None, "timed_out": False, "missing": True,
                "output": f"{argv[0]}: {exc.strerror or exc}", "duration_s": 0.0}  # fmt: skip
    tail = _Tail(proc.stdout)
    timed_out = False
    try:
        proc.wait(timeout=max(timeout, 0.1))
    except subprocess.TimeoutExpired:
        timed_out = True
        _kill_tree(proc)
        proc.wait()
    else:
        if sys.platform != "win32":  # reap anything the step left running in its group
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.killpg(proc.pid, signal.SIGKILL)
    return {
        "exit_code": None if timed_out else proc.returncode,
        "timed_out": timed_out,
        "output": tail.text(),
        "duration_s": round(time.monotonic() - started, 3),
    }


def _environment(workspace: Path, tmp: Path, report: Path) -> dict[str, str]:
    """A scrubbed environment: no host secrets (API keys, tokens) reach generated code."""
    env = {
        "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
        "HOME": str(tmp),
        "TMPDIR": str(tmp),
        "LANG": "C.UTF-8",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONHASHSEED": "0",
        "PYTHONUNBUFFERED": "1",
        "PYTHONPATH": str(PLUGIN_DIR),
        "AUTOSWE_SANDBOX_REPORT": str(report),
        "MYPY_CACHE_DIR": str(tmp / "mypy-cache"),
        "RUFF_CACHE_DIR": str(tmp / "ruff-cache"),
    }
    if sys.platform == "win32":  # the interpreter needs these on Windows; not secrets
        for key in ("SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT"):
            if key in os.environ:
                env[key] = os.environ[key]
        env["TEMP"] = env["TMP"] = str(tmp)
    return env


def _extract(data: bytes, workspace: Path) -> None:
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:") as archive:
        for member in archive.getmembers():
            if not (member.isfile() or member.isdir()):
                raise ValueError(f"unsupported archive member: {member.name}")
        archive.extractall(workspace, filter="data")


def _lint(step: dict[str, Any]) -> dict[str, Any]:
    issues: list[str] = []
    count = 0
    if step["exit_code"] in (0, 1):
        try:
            # ruff prints JSON on stdout; anything after it is a warning on stderr.
            start = step["output"].index("[")
            entries, _ = json.JSONDecoder().raw_decode(step["output"][start:])
            count = len(entries)
            for entry in entries[:MAX_ISSUES]:
                location = entry.get("location") or {}
                issues.append(
                    f"{_rel(str(entry.get('filename', '')))}:{location.get('row', '?')}: "
                    f"{entry.get('code')} {entry.get('message')}"
                )
        except ValueError:
            pass
    return _check(step, count, issues)


_WORKSPACE = Path(".")


def _rel(path: str) -> str:
    try:
        return Path(path).resolve().relative_to(_WORKSPACE).as_posix()
    except ValueError:
        return path


def _typecheck(step: dict[str, Any]) -> dict[str, Any]:
    lines = [line for line in step["output"].splitlines() if ": error:" in line]
    return _check(step, len(lines), lines[:MAX_ISSUES])


def _check(step: dict[str, Any], count: int, issues: list[str]) -> dict[str, Any]:
    if step["timed_out"]:
        status = "timeout"
    elif step["exit_code"] == 0:
        status = "passed"
    elif count:
        status = "failed"
    else:  # crashed or not installed: say so instead of claiming a verdict
        status = "error"
        issues = step["output"].strip().splitlines()[-5:]
    return {
        "status": status,
        "issue_count": count,
        "issues": issues,
        "duration_s": step["duration_s"],
    }


def _cgroup_events() -> dict[str, int]:
    """OOM kills and fork refusals counted by the container's own cgroup (cgroup v2)."""
    counters: dict[str, int] = {}
    for file, key, name in (
        ("memory.events", "oom_kill", "oom_kill"),
        ("pids.events", "max", "pids_max"),
    ):
        try:
            for line in Path("/sys/fs/cgroup", file).read_text().splitlines():
                field, _, value = line.partition(" ")
                if field == key:
                    counters[name] = int(value)
        except (OSError, ValueError):
            continue
    return counters


def _typecheck_targets(workspace: Path) -> list[str]:
    packages = sorted(
        p.name for p in workspace.iterdir() if p.is_dir() and (p / "__init__.py").is_file()
    )
    modules = sorted(p.name for p in workspace.glob("*.py"))
    return packages + modules


Remaining = Any  # () -> float: seconds left of the shared deadline


def _verdict(step: dict[str, Any], summary: dict[str, Any], tool: str) -> dict[str, Any]:
    """The run's status from the test step, for stacks whose runner has no "no tests" code."""
    failed = summary.get("failed", 0) + summary.get("errors", 0)
    if step.get("missing"):
        return {"status": "error", "message": f"{tool} is not available: {step['output']}"}
    if step["timed_out"]:
        return {"status": "timeout", "message": f"{tool} did not finish in time"}
    if step["exit_code"] == 0 and failed == 0 and summary.get("collected", 0) == 0:
        return {"status": "no_tests", "message": f"{tool} found no tests"}
    if step["exit_code"] == 0 and failed == 0:
        return {"status": "passed"}
    return {"status": "failed", "message": f"{tool} exited with {step['exit_code']}"}


def _summary(step: dict[str, Any]) -> dict[str, Any]:
    return {
        "exit_code": step["exit_code"],
        "duration_s": step["duration_s"],
        "output_tail": step["output"][-4_000:],
    }


def _check_python(
    workspace: Path, tmp: Path, remaining: Remaining, typecheck: bool
) -> dict[str, Any]:
    report = tmp / "pytest-report.json"
    env = _environment(workspace, tmp, report)
    python = sys.executable
    result: dict[str, Any] = {}
    lint = _run([python, "-m", "ruff", "check", "--no-cache", "--output-format=json", "."],
                workspace, env, remaining())  # fmt: skip
    result["lint"] = _lint(lint)
    tests = _run(
        [python, "-m", "pytest", "-q", "-p", "no:cacheprovider",
         "-p", "autoswe_sandbox_report", "--rootdir", str(workspace)],
        workspace, env, remaining(),
    )  # fmt: skip
    summary = _summary(tests)
    if report.is_file():
        with contextlib.suppress(ValueError):
            summary.update(json.loads(report.read_text(encoding="utf-8")))
    result["tests"] = summary
    if tests["timed_out"]:
        result.update(status="timeout", message="pytest did not finish in time")
    elif tests["exit_code"] == 0 and summary.get("passed", 0) > 0:
        result["status"] = "passed"
    elif tests["exit_code"] == 5:
        result.update(status="no_tests", message="pytest collected no tests")
    else:
        result.update(status="failed", message=f"pytest exited with {tests['exit_code']}")
    if typecheck and result["status"] != "timeout" and remaining() > 1:
        targets = _typecheck_targets(workspace)
        if targets:
            mypy = _run(
                [python, "-m", "mypy", "--ignore-missing-imports", "--no-incremental",
                 "--no-error-summary", "--cache-dir", str(tmp / "mypy-cache"), *targets],
                workspace, env, remaining(),
            )  # fmt: skip
            result["typecheck"] = _typecheck(mypy)
    return result


# --- TypeScript: eslint, tsc --noEmit, node --test ---------------------------------------------

NODE_MODULES = Path(
    os.environ.get("AUTOSWE_NODE_MODULES", "/opt/autoswe-sandbox/node/node_modules")
)
DEFAULT_ESLINT_CONFIG = PLUGIN_DIR / "node" / "eslint.config.mjs"


def _link_node_modules(workspace: Path) -> None:
    """The sandbox's pinned dev dependencies, visible to the project as its node_modules."""
    target = workspace / "node_modules"
    if target.exists() or target.is_symlink() or not NODE_MODULES.is_dir():
        return
    with contextlib.suppress(OSError):  # e.g. no symlink privilege on a Windows dev machine
        target.symlink_to(NODE_MODULES, target_is_directory=True)


def _eslint(step: dict[str, Any]) -> dict[str, Any]:
    issues: list[str] = []
    count = 0
    if step["exit_code"] in (0, 1):
        try:
            start = step["output"].index("[")
            results, _ = json.JSONDecoder().raw_decode(step["output"][start:])
            for file in results:
                for message in file.get("messages", []):
                    count += 1
                    if len(issues) < MAX_ISSUES:
                        issues.append(
                            f"{_rel(str(file.get('filePath', '')))}:{message.get('line', '?')}: "
                            f"{message.get('ruleId') or 'error'} {message.get('message', '')}"
                        )
        except ValueError:
            pass
    return _check(step, count, issues)


def _tsc(step: dict[str, Any]) -> dict[str, Any]:
    lines = [line for line in step["output"].splitlines() if ": error TS" in line]
    return _check(step, len(lines), lines[:MAX_ISSUES])


def _junit(path: Path) -> dict[str, Any]:
    """Counts and failures from node's JUnit reporter (stdlib XML; the file is ours)."""
    from xml.etree import ElementTree  # noqa: PLC0415 - produced in the sandbox

    counts = {"collected": 0, "passed": 0, "failed": 0, "errors": 0, "skipped": 0}
    failures: list[dict[str, Any]] = []
    try:
        root = ElementTree.parse(path).getroot()  # noqa: S314
    except (OSError, ElementTree.ParseError):
        return counts

    def walk(node: Any, suites: list[str]) -> None:
        for child in node:
            if child.tag == "testsuite":
                walk(child, [*suites, child.get("name", "")])
            elif child.tag == "testcase":
                counts["collected"] += 1
                failure = child.find("failure")
                if failure is not None:
                    counts["failed"] += 1
                    if len(failures) < 50:
                        name = " > ".join([*suites, child.get("name", "")])
                        file = _rel(child.get("file", "")) or "test"
                        details = _relative_paths(failure.text or "")[-2_000:]
                        message = (failure.get("message") or "failed").strip().splitlines()
                        failures.append(
                            {"nodeid": f"{file}::{name}", "phase": "call",
                             "message": " ".join(message)[:300], "details": details}
                        )  # fmt: skip
                elif child.find("skipped") is not None:
                    counts["skipped"] += 1
                else:
                    counts["passed"] += 1

    walk(root, [])
    return {**counts, "failures": failures}


def _relative_paths(text: str) -> str:
    root = str(_WORKSPACE)
    return text.replace(root + os.sep, "").replace(root + "/", "").replace("\\", "/")


def _check_typescript(
    workspace: Path, tmp: Path, remaining: Remaining, typecheck: bool
) -> dict[str, Any]:
    env = _environment(workspace, tmp, tmp / "unused")
    env["NODE_OPTIONS"] = "--disable-warning=ExperimentalWarning"
    node = os.environ.get("AUTOSWE_NODE", "node")
    _link_node_modules(workspace)
    result: dict[str, Any] = {}
    eslint = [node, str(NODE_MODULES / "eslint" / "bin" / "eslint.js"), "--format", "json"]
    if not any(workspace.glob("eslint.config.*")):
        eslint += ["--config", str(DEFAULT_ESLINT_CONFIG)]
    result["lint"] = _eslint(_run([*eslint, "."], workspace, env, remaining()))
    report = tmp / "junit.xml"
    tests = _run(
        [node, "--test", "--test-reporter=junit", f"--test-reporter-destination={report}",
         "--test-reporter=dot", "--test-reporter-destination=stdout", "**/*.test.ts"],
        workspace, env, remaining(),
    )  # fmt: skip
    summary = {**_summary(tests), **_junit(report)}
    result["tests"] = summary
    result.update(_verdict(tests, summary, "node --test"))
    if typecheck and result["status"] != "timeout" and remaining() > 1:
        tsc = [node, str(NODE_MODULES / "typescript" / "bin" / "tsc"), "--noEmit", "-p", "."]
        result["typecheck"] = _tsc(_run(tsc, workspace, env, remaining()))
    return result


# --- Go: gofmt -l, go vet, go test -json -------------------------------------------------------

GO_CACHE_SEED = Path(os.environ.get("AUTOSWE_GO_CACHE_SEED", "/opt/autoswe-sandbox/go/cache"))


def _go_environment(workspace: Path, tmp: Path) -> dict[str, str]:
    env = _environment(workspace, tmp, tmp / "unused")
    cache = tmp / "gocache"
    if GO_CACHE_SEED.is_dir() and not cache.exists():
        # The standard library, compiled when the image was built: copying it saves a cold build.
        shutil.copytree(GO_CACHE_SEED, cache, symlinks=False)
    env.update({
        "GOCACHE": str(cache), "GOPATH": str(tmp / "gopath"), "GOMODCACHE": str(tmp / "gomod"),
        "GOFLAGS": "-mod=mod", "GOPROXY": "off", "GOSUMDB": "off", "GOTOOLCHAIN": "local",
        "GOTELEMETRY": "off", "CGO_ENABLED": "0",
    })  # fmt: skip
    return env


def _gofmt(step: dict[str, Any]) -> dict[str, Any]:
    files = [line.strip() for line in step["output"].splitlines() if line.strip().endswith(".go")]
    if step.get("missing") or step["timed_out"] or step["exit_code"] != 0:
        return _check(step, 0, [])
    status = "failed" if files else "passed"
    issues = [f"{file}: not gofmt-formatted" for file in files[:MAX_ISSUES]]
    return {"status": status, "issue_count": len(files), "issues": issues,
            "duration_s": step["duration_s"]}  # fmt: skip


def _go_vet(step: dict[str, Any]) -> dict[str, Any]:
    lines = [line for line in step["output"].splitlines() if ".go:" in line]
    return _check(step, len(lines), lines[:MAX_ISSUES])


def _go_test_events(output: str) -> dict[str, Any]:
    counts = {"collected": 0, "passed": 0, "failed": 0, "errors": 0, "skipped": 0}
    outputs: dict[tuple[str, str], list[str]] = {}
    failures: list[dict[str, Any]] = []
    build: dict[str, list[str]] = {}
    for line in output.splitlines():
        if not line.startswith("{"):
            continue
        try:
            event = json.loads(line)
        except ValueError:
            continue
        action, package, test = event.get("Action"), event.get("Package", ""), event.get("Test")
        if action == "build-output":
            build.setdefault(event.get("ImportPath", package), []).append(event.get("Output", ""))
        elif action == "output":
            outputs.setdefault((package, test or ""), []).append(event.get("Output", ""))
        elif test and action in {"pass", "fail", "skip"}:
            counts["collected"] += 1
            counts[{"pass": "passed", "fail": "failed", "skip": "skipped"}[action]] += 1
            if action == "fail" and len(failures) < 50:
                text = "".join(outputs.get((package, test), []))
                noise = ("=== RUN", "--- FAIL")
                lines = [ln.strip() for ln in text.splitlines()
                         if ln.strip() and not ln.strip().startswith(noise)]  # fmt: skip
                failures.append({"nodeid": f"{package}::{test}", "phase": "call",
                                 "message": (lines[0] if lines else "failed")[:300],
                                 "details": _relative_paths(text)[-2_000:]})  # fmt: skip
        elif not test and action == "fail" and counts["collected"] == 0:
            # A build failure is reported under the test binary's import path (FailedBuild).
            built = build.get(event.get("FailedBuild", ""), []) or build.get(package, [])
            text = "".join(built or outputs.get((package, ""), []))
            if text.strip():
                counts["errors"] += 1
                first = next((ln for ln in text.splitlines() if ".go:" in ln), text.strip())
                failures.append({"nodeid": package or "build", "phase": "collect",
                                 "message": first.strip()[:300],
                                 "details": _relative_paths(text)[-2_000:]})  # fmt: skip
    return {**counts, "failures": failures}


def _go_text(output: str) -> str:
    """go test -json output as the text a person would have seen."""
    parts = []
    for line in output.splitlines():
        try:
            event = json.loads(line) if line.startswith("{") else None
        except ValueError:
            event = None
        parts.append(str(event.get("Output", "")) if isinstance(event, dict) else line + "\n")
    return "".join(parts)


def _check_go(workspace: Path, tmp: Path, remaining: Remaining, typecheck: bool) -> dict[str, Any]:
    env = _go_environment(workspace, tmp)
    result: dict[str, Any] = {
        "lint": _gofmt(_run(["gofmt", "-l", "."], workspace, env, remaining()))
    }
    tests = _run(["go", "test", "-json", "./..."], workspace, env, remaining())
    summary = {**_summary(tests), **_go_test_events(tests["output"])}
    summary["output_tail"] = _go_text(tests["output"])[-4_000:]
    result["tests"] = summary
    result.update(_verdict(tests, summary, "go test"))
    if typecheck and result["status"] != "timeout" and remaining() > 1:
        result["typecheck"] = _go_vet(_run(["go", "vet", "./..."], workspace, env, remaining()))
    return result


def main(argv: list[str] | None = None) -> int:
    global _WORKSPACE  # noqa: PLW0603 - one run per process
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--stdin-tar", action="store_true", help="Read the workspace as a tar")
    parser.add_argument("--max-archive-bytes", type=int, default=8_000_000)
    parser.add_argument("--deadline-s", type=float, default=120.0)
    parser.add_argument("--no-typecheck", action="store_true")
    parser.add_argument("--cgroup-events", action="store_true")
    parser.add_argument("--stack", choices=["python", "typescript", "go"], default="python")
    args = parser.parse_args(argv)

    deadline: float = time.monotonic() + float(args.deadline_s)
    workspace: Path = args.workspace.resolve()
    _WORKSPACE = workspace
    result: dict[str, Any] = {"status": "error", "message": ""}
    before = _cgroup_events() if args.cgroup_events else {}

    if args.stdin_tar:
        data = sys.stdin.buffer.read(args.max_archive_bytes + 1)
        if len(data) > args.max_archive_bytes:
            message = f"workspace archive exceeds {args.max_archive_bytes} bytes"
            result.update(status="resource_limit", limit="archive", message=message)
            print(RESULT_PREFIX + json.dumps(result), flush=True)  # noqa: T201
            return 0
        try:
            _extract(data, workspace)
        except (tarfile.TarError, ValueError, OSError) as exc:
            result["message"] = f"could not unpack the workspace: {exc}"
            print(RESULT_PREFIX + json.dumps(result), flush=True)  # noqa: T201
            return 0

    with tempfile.TemporaryDirectory(prefix="autoswe-") as scratch:
        tmp = Path(scratch)

        def remaining() -> float:
            return deadline - time.monotonic()

        check = {"python": _check_python, "typescript": _check_typescript, "go": _check_go}
        result.update(check[args.stack](workspace, tmp, remaining, not args.no_typecheck))

    if args.cgroup_events:
        after = _cgroup_events()
        if after.get("oom_kill", 0) > before.get("oom_kill", 0):
            message = "a process was killed for exceeding the memory limit"
            result.update(status="resource_limit", limit="memory", message=message)
        elif after.get("pids_max", 0) > before.get("pids_max", 0):
            result.update(status="resource_limit", limit="pids",
                          message="the process limit refused new processes")  # fmt: skip
    print(RESULT_PREFIX + json.dumps(result), flush=True)  # noqa: T201
    return 0


if __name__ == "__main__":
    sys.exit(main())
