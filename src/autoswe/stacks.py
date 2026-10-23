"""Target stacks a job can generate: what each one's project looks like and how it is checked.

Every stack has a source suffix (for traceability checks), a dependency manifest, a way to tell
test files apart, and a sandbox profile of the same name that lints, type checks and tests it.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import PurePosixPath


class Stack(StrEnum):
    PYTHON = "python"
    TYPESCRIPT = "typescript"
    GO = "go"


@dataclass(frozen=True, slots=True)
class StackProfile:
    stack: Stack
    label: str
    source_suffix: str
    manifests: tuple[str, ...]
    lint: str
    typecheck: str
    tests: str

    def is_test(self, path: str) -> bool:
        name = PurePosixPath(path).name
        if self.stack is Stack.PYTHON:
            return name.startswith("test_") and name.endswith(".py")
        if self.stack is Stack.TYPESCRIPT:
            return name.endswith(".test.ts")
        return name.endswith("_test.go")


PROFILES: dict[Stack, StackProfile] = {
    Stack.PYTHON: StackProfile(
        Stack.PYTHON, "Python 3.12 + FastAPI + SQLAlchemy", ".py",
        ("pyproject.toml", "requirements.txt"), "ruff", "mypy", "pytest",
    ),
    Stack.TYPESCRIPT: StackProfile(
        Stack.TYPESCRIPT, "TypeScript on Node.js 24 (node:http, node:test)", ".ts",
        ("package.json",), "eslint + typescript-eslint", "tsc --noEmit", "node --test",
    ),
    Stack.GO: StackProfile(
        Stack.GO, "Go 1.27 (net/http, standard library only)", ".go",
        ("go.mod",), "gofmt", "go vet", "go test",
    ),
}  # fmt: skip


def profile(stack: Stack | str) -> StackProfile:
    return PROFILES[Stack(stack)]
