"""Run generated projects' checks and tests in an isolated sandbox (see docs/adr/0001)."""

from autoswe.sandbox.models import (
    CheckResult,
    SandboxLimits,
    SandboxResult,
    SandboxStatus,
    TestFailure,
    TestSummary,
)

__all__ = [
    "CheckResult",
    "SandboxLimits",
    "SandboxResult",
    "SandboxStatus",
    "TestFailure",
    "TestSummary",
]
