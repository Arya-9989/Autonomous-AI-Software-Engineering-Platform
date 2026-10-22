"""Sandbox backends: where a generated project's checks and tests actually run.

* :class:`DockerSandbox` - one throw-away container per run: no network, read-only root file
  system, a size-capped tmpfs workspace, an unprivileged user, every capability dropped, no
  privilege escalation, CPU, memory and pid limits, and a wall-clock timeout enforced from the
  outside. Nothing from the host is mounted: the workspace is streamed in as a tar on stdin.
  Whoever runs this backend can start containers, which is root-equivalent on the host, so in
  a deployment it runs only inside the separate sandbox runner (see ``docs/adr/0001``).
* :class:`RemoteSandbox` - the API side of that split: it posts the files to the runner's
  narrow job API and gets a :class:`SandboxResult` back. It holds no Docker access at all.
* :class:`LocalProcessSandbox` - development fallback that runs the checks as a subprocess of
  this process. **It is not a security boundary**: no network or file-system isolation, only a
  wall-clock timeout (plus address-space and CPU rlimits on POSIX) and a scrubbed environment.
"""

from __future__ import annotations

import asyncio
import contextlib
import io
import json
import logging
import os
import signal
import subprocess
import sys
import tarfile
import tempfile
import time
import uuid
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

import httpx2
from opentelemetry import propagate

from autoswe.pipeline.artifacts import GeneratedFile
from autoswe.pipeline.validation import normalize_path
from autoswe.sandbox.models import SandboxLimits, SandboxResult, SandboxStatus

logger = logging.getLogger(__name__)

ENTRY_DIR = Path(__file__).resolve().parent / "inside"
RESULT_PREFIX = "AUTOSWE_SANDBOX_RESULT "
# Paths inside the sandbox image (sandbox/Dockerfile copies ``inside/`` there).
IMAGE_ENTRY = "/opt/autoswe-sandbox/entry.py"
IMAGE_WORKSPACE = "/workspace"
IMAGE_TMP = "/tmp"  # noqa: S108 - a private tmpfs inside the sandbox container, not the host's
_TMPFS_FLAGS = "rw,nosuid,nodev,noexec,mode=1777"
SANDBOX_UID = "10002:10002"
CONTAINER_LABEL = "autoswe.sandbox"
# Container start-up and teardown are not the code's time: allow this on top of the limit.
STARTUP_GRACE_S = 15.0
_STDERR_CAP = 64_000


@runtime_checkable
class Sandbox(Protocol):
    name: str

    async def run(self, files: Sequence[GeneratedFile]) -> SandboxResult: ...


def workspace_archive(files: Sequence[GeneratedFile]) -> bytes:
    """A deterministic tar of the project (regular files only, fixed owner, mode and mtime)."""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w", format=tarfile.PAX_FORMAT) as archive:
        for file in sorted(files, key=lambda f: f.path):
            path = normalize_path(file.path)
            if path is None:  # the pipeline already rejected it; never let it reach a sandbox
                continue
            data = file.content.encode()
            info = tarfile.TarInfo(path)
            info.size, info.mode, info.mtime = len(data), 0o644, 0
            archive.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


def parse_entry_output(stdout: str) -> dict[str, Any] | None:
    """The entry script's result: the last line carrying the result prefix."""
    for line in reversed(stdout.splitlines()):
        if line.startswith(RESULT_PREFIX):
            try:
                parsed = json.loads(line[len(RESULT_PREFIX) :])
            except ValueError:
                return None
            return parsed if isinstance(parsed, dict) else None
    return None


def _result(
    payload: dict[str, Any] | None,
    *,
    backend: str,
    isolation: str,
    duration: float,
    exit_code: int | None,
    fallback: str,
) -> SandboxResult:
    if payload is None:
        return SandboxResult(
            status=SandboxStatus.ERROR,
            backend=backend,
            isolation=isolation,
            message=fallback,
            duration_s=round(duration, 3),
            exit_code=exit_code,
        )
    try:
        result = SandboxResult.model_validate(
            {**payload, "backend": backend, "isolation": isolation}
        )
    except ValueError as exc:
        return SandboxResult(
            status=SandboxStatus.ERROR,
            backend=backend,
            isolation=isolation,
            message=f"unreadable sandbox report: {type(exc).__name__}",
        )
    return result.model_copy(update={"duration_s": round(duration, 3), "exit_code": exit_code})


async def _read_capped(stream: asyncio.StreamReader | None, cap: int) -> tuple[bytes, bool]:
    """Read a stream to EOF, keeping at most ``cap`` bytes; returns (data, overflowed)."""
    if stream is None:
        return b"", False
    chunks: list[bytes] = []
    size, overflow = 0, False
    while chunk := await stream.read(65_536):
        if size < cap:
            chunks.append(chunk[: cap - size])
        size += len(chunk)
        overflow = overflow or size > cap
    return b"".join(chunks), overflow


class DockerSandbox:
    name = "docker"

    def __init__(
        self,
        image: str,
        limits: SandboxLimits,
        *,
        runtime: str | None = None,
        docker: str | Sequence[str] = "docker",
        pool: str = "default",
        max_concurrent: int = 2,
        startup_grace_s: float = STARTUP_GRACE_S,
    ) -> None:
        self.image = image
        self.startup_grace_s = startup_grace_s
        self.limits = limits
        self.runtime = runtime
        # The CLI (Docker, rootless Docker or a Docker-compatible CLI); a command prefix in tests.
        self._cli = [docker] if isinstance(docker, str) else list(docker)
        self.pool = pool
        self._slots = asyncio.Semaphore(max_concurrent)

    @property
    def isolation(self) -> str:
        runtime = f", runtime {self.runtime}" if self.runtime else ""
        return (
            f"container per run{runtime}: no network, read-only root fs, tmpfs workspace, "
            f"uid {SANDBOX_UID.partition(':')[0]}, all capabilities dropped, no-new-privileges, "
            f"{self.limits.memory_mb} MiB memory, {self.limits.cpus:g} CPU, "
            f"{self.limits.pids} pids, {self.limits.timeout_s:g} s"
        )

    def run_args(self, container: str) -> list[str]:
        """The full ``docker run`` argument vector. Built here only; callers supply nothing."""
        limits = self.limits
        args = [
            "run", "--interactive", "--pull=never", "--name", container,
            "--label", f"{CONTAINER_LABEL}=1", "--label", f"{CONTAINER_LABEL}.pool={self.pool}",
            "--network", "none",
            "--read-only",
            "--tmpfs", f"{IMAGE_WORKSPACE}:{_TMPFS_FLAGS},size={limits.workspace_mb}m",
            "--tmpfs", f"{IMAGE_TMP}:{_TMPFS_FLAGS},size={limits.tmp_mb}m",
            "--user", SANDBOX_UID,
            "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges:true",
            "--memory", f"{limits.memory_mb}m", "--memory-swap", f"{limits.memory_mb}m",
            "--cpus", f"{limits.cpus:g}",
            "--pids-limit", str(limits.pids),
            "--ulimit", "nofile=1024:1024",
            "--ulimit", f"fsize={limits.workspace_mb * 1024 * 1024}",
            "--ipc", "private",
            "--init",
            "--log-driver", "none",
            "--workdir", IMAGE_WORKSPACE,
            "--hostname", "sandbox",
            "--env", "PYTHONDONTWRITEBYTECODE=1",
        ]  # fmt: skip
        if self.runtime:
            args += ["--runtime", self.runtime]
        args += [
            self.image, "python", IMAGE_ENTRY, "--workspace", IMAGE_WORKSPACE, "--stdin-tar",
            "--max-archive-bytes", str(limits.max_archive_bytes),
            "--deadline-s", f"{max(limits.timeout_s - 2, 1):g}",
            "--cgroup-events",
        ]  # fmt: skip
        if not limits.typecheck:
            args.append("--no-typecheck")
        return args

    async def _docker(self, *args: str, limit_s: float = 30.0) -> tuple[int, str]:
        proc = await asyncio.create_subprocess_exec(
            *self._cli, *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT
        )
        try:
            out, _ = await asyncio.wait_for(proc.communicate(), limit_s)
        except TimeoutError:
            proc.kill()
            await proc.wait()
            return -1, "docker command timed out"
        return proc.returncode or 0, out.decode("utf-8", "replace")

    async def ready(self) -> tuple[bool, str]:
        code, out = await self._docker("image", "inspect", "--format", "{{.Id}}", self.image)
        if code != 0:
            return False, f"sandbox image {self.image} is not available: {out.strip()[:200]}"
        return True, "ok"

    async def remove_leftovers(self) -> int:
        """Remove containers of this pool left by a previous, crashed runner process."""
        code, out = await self._docker(
            "ps", "-aq", "--filter", f"label={CONTAINER_LABEL}.pool={self.pool}"
        )
        ids = out.split() if code == 0 else []
        if ids:
            await self._docker("rm", "-f", *ids)
        return len(ids)

    async def run(self, files: Sequence[GeneratedFile]) -> SandboxResult:
        archive = workspace_archive(files)
        if len(archive) > self.limits.max_archive_bytes:
            return SandboxResult(
                status=SandboxStatus.RESOURCE_LIMIT, backend=self.name, isolation=self.isolation,
                limit="archive", message=f"workspace is {len(archive)} bytes",
            )  # fmt: skip
        async with self._slots:
            return await self._run(archive)

    async def _run(self, archive: bytes) -> SandboxResult:
        container = f"autoswe-sbx-{uuid.uuid4().hex[:12]}"
        started = time.monotonic()
        hard_limit = self.limits.timeout_s + self.startup_grace_s
        try:
            proc = await asyncio.create_subprocess_exec(
                *self._cli, *self.run_args(container),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )  # fmt: skip
        except OSError as exc:
            return SandboxResult(
                status=SandboxStatus.ERROR, backend=self.name, isolation=self.isolation,
                message=f"cannot start the docker CLI: {exc}",
            )  # fmt: skip
        timed_out = False
        try:
            assert proc.stdin is not None
            with contextlib.suppress(BrokenPipeError, ConnectionResetError):
                proc.stdin.write(archive)
                await proc.stdin.drain()
                proc.stdin.close()
            reads = asyncio.gather(
                _read_capped(proc.stdout, self.limits.max_output_bytes),
                _read_capped(proc.stderr, _STDERR_CAP),
            )
            try:
                (stdout, overflow), (stderr, _) = await asyncio.wait_for(reads, hard_limit)
                await proc.wait()
            except TimeoutError:
                timed_out, stdout, overflow, stderr = True, b"", False, b""
                await self._docker("kill", container)
                with contextlib.suppress(ProcessLookupError):
                    proc.kill()
                await proc.wait()
            state = await self._state(container)
        finally:
            await self._docker("rm", "-f", container)
        duration = time.monotonic() - started
        return self._classify(
            stdout.decode("utf-8", "replace"), stderr.decode("utf-8", "replace"),
            state, proc.returncode, duration, timed_out=timed_out, overflow=overflow,
        )  # fmt: skip

    async def _state(self, container: str) -> dict[str, Any]:
        code, out = await self._docker("inspect", "--format", "{{json .State}}", container)
        if code != 0:
            return {}
        try:
            state = json.loads(out)
        except ValueError:
            return {}
        return state if isinstance(state, dict) else {}

    def _classify(
        self,
        stdout: str,
        stderr: str,
        state: dict[str, Any],
        exit_code: int | None,
        duration: float,
        *,
        timed_out: bool,
        overflow: bool,
    ) -> SandboxResult:
        common = {"backend": self.name, "isolation": self.isolation,
                  "duration_s": round(duration, 3), "exit_code": exit_code}  # fmt: skip
        if timed_out:
            return SandboxResult(
                status=SandboxStatus.TIMEOUT,
                message=f"killed after {self.limits.timeout_s:g} s wall-clock",
                **common,
            )
        if overflow:
            return SandboxResult(
                status=SandboxStatus.RESOURCE_LIMIT, limit="output",
                message=f"more than {self.limits.max_output_bytes} bytes on stdout",
                **common,
            )  # fmt: skip
        payload = parse_entry_output(stdout)
        if state.get("OOMKilled") and (payload is None or payload.get("status") != "timeout"):
            base = payload or {}
            return SandboxResult.model_validate(
                {**base, **common, "status": "resource_limit", "limit": "memory",
                 "message": f"killed at the {self.limits.memory_mb} MiB memory limit"}
            )  # fmt: skip
        if payload is None and exit_code == 137:
            return SandboxResult(
                status=SandboxStatus.RESOURCE_LIMIT, limit="memory",
                message="the sandbox was killed (SIGKILL) before it reported",
                **common,
            )  # fmt: skip
        return _result(
            payload, backend=self.name, isolation=self.isolation, duration=duration,
            exit_code=exit_code,
            fallback=f"sandbox produced no result (exit {exit_code}): {stderr.strip()[-300:]}",
        )  # fmt: skip


def _posix_limits(memory_mb: int, cpu_s: int) -> Any:
    def apply() -> None:  # runs in the child between fork and exec
        if sys.platform != "win32":
            import resource  # noqa: PLC0415 - POSIX only

            # Virtual size, not resident memory: interpreters reserve far more than they touch.
            address_space = memory_mb * 4 * 1024 * 1024
            resource.setrlimit(resource.RLIMIT_AS, (address_space, address_space))
            resource.setrlimit(resource.RLIMIT_CPU, (cpu_s, cpu_s))

    return apply


def _kill_process_tree(pid: int) -> None:
    if sys.platform != "win32":
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(pid, signal.SIGKILL)
    else:
        subprocess.run(  # noqa: S603 - fixed argv
            ["taskkill", "/F", "/T", "/PID", str(pid)],  # noqa: S607
            capture_output=True,
            check=False,
        )


class LocalProcessSandbox:
    """Development fallback. Runs generated code on this machine, with almost no isolation."""

    name = "local"

    def __init__(self, limits: SandboxLimits, *, python: str = sys.executable) -> None:
        self.limits = limits
        self.python = python

    @property
    def isolation(self) -> str:
        enforced = (
            "wall-clock timeout, address-space and CPU rlimits"
            if sys.platform != "win32"
            else "wall-clock timeout only"
        )
        return (
            f"NONE - local subprocess (development only): {enforced}, scrubbed environment; "
            "no network or file-system isolation"
        )

    async def run(self, files: Sequence[GeneratedFile]) -> SandboxResult:
        with tempfile.TemporaryDirectory(prefix="autoswe-sbx-") as scratch:
            workspace = Path(scratch) / "workspace"
            workspace.mkdir()
            for file in files:
                path = normalize_path(file.path)
                if path is None:
                    continue
                target = workspace / path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(file.content, encoding="utf-8", newline="\n")
            return await self._run(workspace)

    async def _run(self, workspace: Path) -> SandboxResult:
        limits = self.limits
        argv = [self.python, str(ENTRY_DIR / "entry.py"), "--workspace", str(workspace),
                "--deadline-s", f"{max(limits.timeout_s - 2, 1):g}"]  # fmt: skip
        if not limits.typecheck:
            argv.append("--no-typecheck")
        keep = ("PATH", "SYSTEMROOT", "WINDIR")
        env = {key: os.environ[key] for key in keep if key in os.environ}
        kwargs: dict[str, Any] = {}
        if sys.platform != "win32":
            kwargs["start_new_session"] = True
            kwargs["preexec_fn"] = _posix_limits(limits.memory_mb, int(limits.timeout_s) + 5)
        else:
            kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        started = time.monotonic()
        proc = await asyncio.create_subprocess_exec(
            *argv, cwd=workspace, env=env, stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, **kwargs,
        )  # fmt: skip
        timed_out = False
        try:
            (stdout, overflow), (stderr, _) = await asyncio.wait_for(
                asyncio.gather(
                    _read_capped(proc.stdout, limits.max_output_bytes),
                    _read_capped(proc.stderr, _STDERR_CAP),
                ),
                limits.timeout_s + 5,
            )
            await proc.wait()
        except TimeoutError:
            timed_out, stdout, overflow, stderr = True, b"", False, b""
            _kill_process_tree(proc.pid)
            await proc.wait()
        duration = time.monotonic() - started
        common = {"backend": self.name, "isolation": self.isolation,
                  "duration_s": round(duration, 3), "exit_code": proc.returncode}  # fmt: skip
        if timed_out:
            return SandboxResult(
                status=SandboxStatus.TIMEOUT,
                message=f"killed after {limits.timeout_s:g} s wall-clock",
                **common,
            )
        if overflow:
            return SandboxResult(
                status=SandboxStatus.RESOURCE_LIMIT, limit="output",
                message=f"more than {limits.max_output_bytes} bytes on stdout",
                **common,
            )  # fmt: skip
        return _result(
            parse_entry_output(stdout.decode("utf-8", "replace")),
            backend=self.name, isolation=self.isolation, duration=duration,
            exit_code=proc.returncode,
            fallback=f"sandbox produced no result: {stderr.decode('utf-8', 'replace')[-300:]}",
        )  # fmt: skip


class RemoteSandbox:
    """Client of the sandbox runner's job API. Holds no Docker access."""

    name = "remote"

    def __init__(
        self,
        url: str,
        token: str,
        *,
        timeout_s: float,
        retries: int = 2,
        transport: httpx2.AsyncBaseTransport | None = None,
    ) -> None:
        self.url = url.rstrip("/")
        self._token = token
        self._timeout = httpx2.Timeout(timeout_s + 60.0, connect=10.0)
        self._retries = retries
        self._transport = transport

    def _error(self, message: str) -> SandboxResult:
        return SandboxResult(
            status=SandboxStatus.ERROR, backend=self.name, isolation="unknown", message=message
        )

    async def run(self, files: Sequence[GeneratedFile]) -> SandboxResult:
        body = {"files": [{"path": f.path, "content": f.content} for f in files]}
        headers = {"Authorization": f"Bearer {self._token}"}
        propagate.inject(headers)  # traceparent: the runner's spans join the job's trace
        async with httpx2.AsyncClient(timeout=self._timeout, transport=self._transport) as client:
            for attempt in range(self._retries + 1):
                try:
                    response = await client.post(f"{self.url}/v1/runs", json=body, headers=headers)
                except httpx2.TimeoutException:
                    return self._error("the sandbox runner did not answer in time")
                except httpx2.TransportError as exc:
                    if attempt < self._retries:  # runs are pure functions of the files: retry
                        await asyncio.sleep(2**attempt)
                        continue
                    return self._error(f"cannot reach the sandbox runner: {type(exc).__name__}")
                if response.status_code == 503 and attempt < self._retries:
                    await asyncio.sleep(2**attempt)
                    continue
                if response.status_code != 200:
                    return self._error(f"sandbox runner answered HTTP {response.status_code}")
                try:
                    return SandboxResult.model_validate(response.json())
                except ValueError:
                    return self._error("sandbox runner sent an unreadable result")
        return self._error("sandbox runner busy")  # pragma: no cover - loop always returns
