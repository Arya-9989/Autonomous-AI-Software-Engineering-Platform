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
    proc = subprocess.Popen(  # noqa: S603 - argv is built by this script, not from input
        argv,
        cwd=cwd,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        **kwargs,
    )
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


def main(argv: list[str] | None = None) -> int:  # noqa: PLR0912, PLR0915 - one linear script
    global _WORKSPACE  # noqa: PLW0603 - one run per process
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--stdin-tar", action="store_true", help="Read the workspace as a tar")
    parser.add_argument("--max-archive-bytes", type=int, default=8_000_000)
    parser.add_argument("--deadline-s", type=float, default=120.0)
    parser.add_argument("--no-typecheck", action="store_true")
    parser.add_argument("--cgroup-events", action="store_true")
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
        report = tmp / "pytest-report.json"
        env = _environment(workspace, tmp, report)
        python = sys.executable

        def remaining() -> float:
            return deadline - time.monotonic()

        lint = _run([python, "-m", "ruff", "check", "--no-cache", "--output-format=json", "."],
                    workspace, env, remaining())  # fmt: skip
        result["lint"] = _lint(lint)

        tests = _run(
            [python, "-m", "pytest", "-q", "-p", "no:cacheprovider",
             "-p", "autoswe_sandbox_report", "--rootdir", str(workspace)],
            workspace, env, remaining(),
        )  # fmt: skip
        summary: dict[str, Any] = {
            "exit_code": tests["exit_code"],
            "duration_s": tests["duration_s"],
            "output_tail": tests["output"][-4_000:],
        }
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

        if not args.no_typecheck and result["status"] != "timeout" and remaining() > 1:
            targets = _typecheck_targets(workspace)
            if targets:
                mypy = _run(
                    [python, "-m", "mypy", "--ignore-missing-imports", "--no-incremental",
                     "--no-error-summary", "--cache-dir", str(tmp / "mypy-cache"), *targets],
                    workspace, env, remaining(),
                )  # fmt: skip
                result["typecheck"] = _typecheck(mypy)

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
