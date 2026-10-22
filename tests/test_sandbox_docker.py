"""The Docker sandbox against real containers: results, limits, and escape attempts.

Needs Docker and the sandbox image (``docker build -f sandbox/Dockerfile -t
autoswe-sandbox:local .``). Without them these tests skip, unless ``AUTOSWE_REQUIRE_DOCKER=1``
(set in CI), which turns a missing image into a failure so the suite cannot pass by skipping.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess

import pytest

from autoswe.sandbox.backends import DockerSandbox
from autoswe.sandbox.models import SandboxLimits, SandboxResult, SandboxStatus
from tests import sandbox_projects as projects

IMAGE = os.environ.get("SANDBOX_TEST_IMAGE", "autoswe-sandbox:local")


def _image_available() -> bool:
    if shutil.which("docker") is None:
        return False
    probe = subprocess.run(
        ["docker", "image", "inspect", IMAGE],
        capture_output=True,
        check=False,
    )
    return probe.returncode == 0


if not _image_available():
    if os.environ.get("AUTOSWE_REQUIRE_DOCKER") == "1":
        raise RuntimeError(f"AUTOSWE_REQUIRE_DOCKER=1 but the image {IMAGE} is not available")
    pytest.skip(f"needs Docker and the {IMAGE} image", allow_module_level=True)

pytestmark = pytest.mark.docker


def sandbox(**limits: float | int | bool) -> DockerSandbox:
    settings = {"timeout_s": 60.0, "typecheck": False, **limits}
    return DockerSandbox(IMAGE, SandboxLimits(**settings), pool="tests")  # type: ignore[arg-type]


def _failure_messages(result: SandboxResult) -> dict[str, str]:
    assert result.tests is not None
    return {f.nodeid.rsplit("::", 1)[1]: f.message for f in result.tests.failures}


async def test_a_passing_suite_passes_with_lint_and_types() -> None:
    result = await sandbox(typecheck=True).run(projects.passing())
    assert result.status is SandboxStatus.PASSED, result.describe()
    assert result.tests is not None and (result.tests.collected, result.tests.passed) == (2, 2)
    assert result.lint is not None and result.lint.status == "passed"
    assert result.typecheck is not None and result.typecheck.status == "passed"


async def test_failures_come_back_structured() -> None:
    result = await sandbox().run(projects.failing())
    assert result.status is SandboxStatus.FAILED
    messages = _failure_messages(result)
    assert messages["test_add"] == "assert -1 == 5"
    assert result.repairable


async def test_every_escape_attempt_is_refused() -> None:
    result = await sandbox().run(projects.project(projects.ESCAPES))
    assert result.tests is not None
    assert result.tests.passed == 0, "an escape attempt succeeded"
    messages = _failure_messages(result)
    assert len(messages) == 10
    assert "Network is unreachable" in messages["test_reach_the_internet"]
    assert "name resolution" in messages["test_resolve_a_hostname"]
    assert "Network is unreachable" in messages["test_reach_the_docker_host_gateway"]
    assert "Read-only file system" in messages["test_write_outside_the_workspace"]
    assert "Read-only file system" in messages["test_overwrite_the_sandbox_entry_point"]
    assert "Operation not permitted" in messages["test_become_root"]
    assert "CapEff:\t0000000000000000" in messages["test_has_capabilities"]


async def test_a_fork_bomb_hits_the_pid_limit() -> None:
    result = await sandbox(timeout_s=15).run(projects.project(projects.FORK_BOMB))
    assert (result.status, result.limit) == (SandboxStatus.RESOURCE_LIMIT, "pids")
    assert result.duration_s < 40


async def test_a_memory_hog_is_killed_at_the_memory_limit() -> None:
    result = await sandbox(memory_mb=256).run(projects.project(projects.MEMORY_HOG))
    assert (result.status, result.limit) == (SandboxStatus.RESOURCE_LIMIT, "memory")


async def test_an_infinite_loop_times_out() -> None:
    result = await sandbox(timeout_s=8).run(projects.project(projects.INFINITE_LOOP))
    assert result.status is SandboxStatus.TIMEOUT
    assert result.duration_s < 30


async def test_output_floods_and_disk_fills_are_contained() -> None:
    flood = await sandbox().run(projects.project(projects.OUTPUT_FLOOD))
    assert flood.status is SandboxStatus.FAILED
    assert flood.tests is not None and len(flood.tests.output_tail) <= 4_000
    disk = await sandbox(workspace_mb=16).run(projects.project(projects.DISK_FILL))
    assert disk.status is SandboxStatus.FAILED
    assert "No space left on device" in _failure_messages(disk)["test_fill_the_workspace"]


async def test_no_container_is_left_behind() -> None:
    await sandbox().run(projects.passing())
    leftovers = await asyncio.to_thread(
        subprocess.run,
        ["docker", "ps", "-aq", "--filter", "label=autoswe.sandbox.pool=tests"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert leftovers.stdout.strip() == ""
