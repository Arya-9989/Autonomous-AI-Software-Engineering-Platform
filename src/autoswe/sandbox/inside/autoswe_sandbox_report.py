"""pytest plugin loaded inside the sandbox: writes per-test outcomes as JSON.

It is loaded with ``-p autoswe_sandbox_report`` and writes to the file named by
``AUTOSWE_SANDBOX_REPORT``. Paths under the workspace are made relative, so a report does not
depend on where the workspace lives (``/workspace`` in a container, a temp dir locally).

The generated project runs in the same process and could tamper with this plugin's output (as
it could with pytest's own reporting, e.g. from a ``conftest.py``). The sandbox protects the
host from the code; it does not make an adversarial test suite's verdict trustworthy.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any

MAX_DETAILS = 2_000
MAX_FAILURES = 50

_failures: list[dict[str, Any]] = []
_counts = {"passed": 0, "failed": 0, "errors": 0, "skipped": 0}
_root = ""


_WINDOWS_PATH = re.compile(r"[\w.-]+(?:\\[\w.-]+)+\.py")


def _relative(text: str) -> str:
    if _root:
        for prefix in (_root + os.sep, _root + "/", _root):
            text = text.replace(prefix, "")
    if os.sep == "\\":  # report POSIX paths on every platform
        text = _WINDOWS_PATH.sub(lambda match: match.group(0).replace("\\", "/"), text)
    return text


def _record(nodeid: str, phase: str, report: Any) -> None:
    if len(_failures) >= MAX_FAILURES:
        return
    text = _relative(str(getattr(report, "longreprtext", "") or ""))
    lines = [line for line in text.splitlines() if line.strip()]
    # pytest puts the exception summary ("E   AssertionError: ...") near the end.
    errors = [line.strip()[1:].strip() for line in lines if line.lstrip().startswith("E ")]
    message = (errors[0] if errors else (lines[-1] if lines else "failed"))[:300]
    details = text[-MAX_DETAILS:] if len(text) > MAX_DETAILS else text
    _failures.append({"nodeid": nodeid, "phase": phase, "message": message, "details": details})


def pytest_sessionstart(session: Any) -> None:
    global _root  # noqa: PLW0603 - plugin state lives for one pytest session
    _root = str(session.config.rootpath)


def pytest_collectreport(report: Any) -> None:
    if report.failed:
        _counts["errors"] += 1
        _record(_relative(report.nodeid) or "<collection>", "collect", report)


def pytest_runtest_logreport(report: Any) -> None:
    if report.when == "call":
        if report.passed:
            _counts["passed"] += 1
        elif report.failed:
            _counts["failed"] += 1
            _record(report.nodeid, "call", report)
        else:
            _counts["skipped"] += 1
    elif report.skipped and report.when == "setup":
        _counts["skipped"] += 1
    elif report.failed:  # setup or teardown error
        _counts["errors"] += 1
        _record(report.nodeid, report.when, report)


def pytest_sessionfinish(session: Any, exitstatus: int) -> None:
    target = os.environ.get("AUTOSWE_SANDBOX_REPORT")
    if not target:
        return
    payload = {
        "collected": int(getattr(session, "testscollected", 0) or 0),
        "exit_code": int(exitstatus),
        **_counts,
        "failures": _failures,
    }
    with open(target, "w", encoding="utf-8") as handle:
        json.dump(payload, handle)
