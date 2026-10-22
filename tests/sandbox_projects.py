"""Small generated-project fixtures for sandbox tests, including hostile ones.

The hostile projects are written from the attacker's side: each test *asserts that an escape
worked*. In a working sandbox those tests fail, and their failure messages are the evidence of
what the sandbox refused.
"""

from __future__ import annotations

from textwrap import dedent

from autoswe.pipeline.artifacts import GeneratedFile

PYPROJECT = dedent("""
    [project]
    name = "fixture"
    version = "0.1.0"
    requires-python = ">=3.12"

    [tool.ruff]
    line-length = 100

    [tool.pytest.ini_options]
    testpaths = ["tests"]
    pythonpath = ["."]
""").lstrip()

CALC_OK = "def add(a: int, b: int) -> int:\n    return a + b\n"
CALC_BUG = "def add(a: int, b: int) -> int:\n    return a - b\n"
CALC_TESTS = dedent("""
    from app.calc import add


    def test_add() -> None:
        assert add(2, 3) == 5


    def test_add_negative() -> None:
        assert add(-1, 1) == 0
""").lstrip()


def project(tests: str, calc: str = CALC_OK, **extra: str) -> list[GeneratedFile]:
    files = {
        "pyproject.toml": PYPROJECT,
        "app/__init__.py": "",
        "app/calc.py": calc,
        "tests/__init__.py": "",
        "tests/test_calc.py": tests,
        **extra,
    }
    return [GeneratedFile(path=path, content=content) for path, content in files.items()]


def passing() -> list[GeneratedFile]:
    return project(CALC_TESTS)


def failing() -> list[GeneratedFile]:
    return project(CALC_TESTS, calc=CALC_BUG)


ESCAPES = dedent('''
    """Each test asserts that an escape succeeded; in a sandbox every one must fail."""

    import os
    import socket
    from pathlib import Path


    def test_reach_the_internet() -> None:
        socket.create_connection(("1.1.1.1", 443), timeout=3).close()


    def test_resolve_a_hostname() -> None:
        assert socket.getaddrinfo("example.com", 443)


    def test_reach_the_docker_host_gateway() -> None:
        socket.create_connection(("172.17.0.1", 2375), timeout=3).close()


    def test_write_outside_the_workspace() -> None:
        Path("/usr/local/lib/python3.12/site-packages/evil.pth").write_text("import os")


    def test_overwrite_the_sandbox_entry_point() -> None:
        Path("/opt/autoswe-sandbox/entry.py").write_text("print('owned')")


    def test_write_to_etc() -> None:
        Path("/etc/cron.d/evil").write_text("* * * * * root id")


    def test_docker_socket_is_reachable() -> None:
        assert Path("/var/run/docker.sock").exists()


    def test_become_root() -> None:
        os.setuid(0)


    def test_has_capabilities() -> None:
        status = Path("/proc/self/status").read_text()
        effective = next(line for line in status.splitlines() if line.startswith("CapEff"))
        assert int(effective.split()[1], 16) != 0, effective


    def test_host_secrets_leak_into_the_environment() -> None:
        leaked = [k for k in os.environ if "KEY" in k or "TOKEN" in k or "SECRET" in k]
        assert leaked, sorted(os.environ)
''').lstrip()

FORK_BOMB = dedent("""
    import os


    def test_fork_bomb() -> None:
        while True:
            try:
                os.fork()
            except OSError:
                pass
""").lstrip()

MEMORY_HOG = dedent("""
    def test_allocate_four_gigabytes() -> None:
        hog = []
        for _ in range(40):
            hog.append(b"x" * (100 * 1024 * 1024))
        assert len(hog) == 40
""").lstrip()

INFINITE_LOOP = dedent("""
    def test_never_returns() -> None:
        while True:
            pass
""").lstrip()

OUTPUT_FLOOD = dedent("""
    import sys


    def test_flood_stdout() -> None:
        chunk = "x" * 1_000_000
        for _ in range(200):
            sys.stdout.write(chunk)
        assert False, "flooded"
""").lstrip()

DISK_FILL = dedent("""
    from pathlib import Path


    def test_fill_the_workspace() -> None:
        with Path("filler.bin").open("wb") as handle:
            for _ in range(1024):
                handle.write(b"x" * 1024 * 1024)
""").lstrip()
