"""A stand-in for the ``docker`` CLI, driven by ``FAKE_DOCKER_MODE``.

``DockerSandbox`` only talks to Docker through its CLI, so running it against this script tests
its control flow (hard timeout, OOM detection, output caps, cleanup) on any machine. Every
invocation is appended to ``FAKE_DOCKER_LOG`` so tests can check what was asked of Docker.
"""

# ruff: noqa: T201, PLR0911, PLR0912 - a CLI stand-in: it prints and branches per command

from __future__ import annotations

import json
import os
import sys
import time

RESULT = "AUTOSWE_SANDBOX_RESULT "


def main() -> int:
    mode = os.environ.get("FAKE_DOCKER_MODE", "ok")
    args = sys.argv[1:]
    with open(os.environ["FAKE_DOCKER_LOG"], "a", encoding="utf-8") as log:
        log.write(json.dumps(args) + "\n")
    command = args[0] if args else ""
    if command == "run":
        sys.stdin.buffer.read()
        if mode == "hang":
            time.sleep(60)
        elif mode == "ok":
            report = {"status": "passed", "tests": {"collected": 1, "passed": 1}}
            print("noise from the container")
            print(RESULT + json.dumps(report))
        elif mode == "oom":
            print(RESULT + json.dumps({"status": "failed", "message": "pytest exited with -9"}))
            return 137
        elif mode == "sigkill":
            return 137
        elif mode == "flood":
            sys.stdout.write("x" * 3_000_000)
        elif mode == "garbage":
            print(RESULT + "{not json")
        elif mode == "no-image":
            print("Unable to find image 'autoswe-sandbox:local' locally", file=sys.stderr)
            return 125
        return 0
    if command == "inspect":
        if mode == "no-image":
            return 1
        print(json.dumps({"Status": "exited", "OOMKilled": mode == "oom", "ExitCode": 0}))
        return 0
    if command == "image":
        if mode == "no-image":
            print("Error: No such image: autoswe-sandbox:local")
            return 1
        print("sha256:feedface")
        return 0
    if command == "ps":
        print("leftover1\nleftover2")
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
