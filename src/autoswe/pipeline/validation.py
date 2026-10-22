"""Deterministic checks run on every generated project before (and after) the reviewer agent.

LLM output is untrusted input. These checks parse every file with a real parser (``ast`` for
Python, PyYAML, python-hcl2, ``json``, ``tomllib``), enforce path safety and size limits, apply a
handful of Kubernetes/Dockerfile/GitHub Actions best-practice rules and scan for leaked secrets.
"""

from __future__ import annotations

import ast
import io
import json
import re
import tomllib
import unicodedata
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any

import hcl2
import yaml

from autoswe.pipeline.artifacts import (
    ArchitectureSpec,
    GeneratedFile,
    Severity,
    ValidationIssue,
    ValidationReport,
)

_SECRET_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("aws-access-key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("private-key", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----")),
    ("anthropic-key", re.compile(r"\bsk-ant-[A-Za-z0-9_-]{20,}")),
    ("github-token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b")),
)
# Characters Windows cannot store in a file name (":" also covers drive letters and NTFS
# alternate data streams), plus ASCII control characters.
_INVALID_CHARS = re.compile(r'[<>:"|?*\x00-\x1f\x7f]')
# Device names Windows reserves in every directory, with or without an extension ("nul.txt").
_WINDOWS_RESERVED = frozenset(
    {"con", "prn", "aux", "nul"}
    | {f"com{i}" for i in range(1, 10)}
    | {f"lpt{i}" for i in range(1, 10)}
)
# ".git" as a case-insensitive file system or a Windows 8.3 short name sees it.
_GIT_DIR_NAMES = frozenset({".git", "git~1"})
MAX_PATH_DEPTH = 8


@dataclass(frozen=True, slots=True)
class Limits:
    max_files: int = 200
    max_file_bytes: int = 512_000
    max_total_bytes: int = 5_000_000


class _Collector:
    def __init__(self) -> None:
        self.issues: list[ValidationIssue] = []

    def add(
        self,
        severity: Severity,
        rule: str,
        message: str,
        file: str | None = None,
        line: int | None = None,
    ) -> None:
        self.issues.append(
            ValidationIssue(severity=severity, rule=rule, message=message, file=file, line=line)
        )


def _unsafe_segment(part: str) -> bool:
    if part in {"", ".", ".."}:
        return True
    # Windows drops trailing dots and spaces, so ".. " is ".." and ".git." is ".git" there.
    if part != part.rstrip(". "):
        return True
    folded = part.casefold()
    return folded in _GIT_DIR_NAMES or folded.split(".", 1)[0] in _WINDOWS_RESERVED


def normalize_path(raw: str) -> str | None:
    """Return a safe, normalised POSIX relative path, or ``None`` if the path is unsafe.

    Archives are extracted on Linux, macOS and Windows alike, so a path is rejected if it is
    unsafe on any of them: absolute paths, ``..`` and empty segments, ``.git`` in any letter
    case (``.GIT/hooks`` is ``.git/hooks`` on a case-insensitive file system), Unicode format
    characters (HFS+ ignores some, so ``.g<ZWNJ>it`` is ``.git``), names Windows rewrites or
    reserves (trailing dots and spaces, ``CON``, ``nul.txt``) and characters it cannot store.
    """
    candidate = raw.strip()
    if not candidate or "\\" in candidate or candidate.startswith("/"):
        return None
    if _INVALID_CHARS.search(candidate):
        return None
    if any(unicodedata.category(char) == "Cf" for char in candidate):
        return None
    parts = candidate.split("/")
    if len(parts) > MAX_PATH_DEPTH or any(_unsafe_segment(part) for part in parts):
        return None
    return str(PurePosixPath(*parts))


def path_key(path: str) -> str:
    """Identity of a path on a case-insensitive file system (the macOS and Windows default)."""
    return unicodedata.normalize("NFC", path).casefold()


def sanitize_files(
    files: Iterable[GeneratedFile], limits: Limits
) -> tuple[list[GeneratedFile], list[ValidationIssue]]:
    """Drop unsafe/oversized/duplicate files before they are persisted or zipped."""
    collector = _Collector()
    # Keyed by path_key: "README.md" and "readme.md" would overwrite each other when extracted.
    accepted: dict[str, GeneratedFile] = {}
    total = 0
    for file in files:
        path = normalize_path(file.path)
        if path is None:
            collector.add("error", "path-safety", f"Rejected unsafe path {file.path!r}.")
            continue
        size = len(file.content.encode())
        if size > limits.max_file_bytes:
            collector.add(
                "error", "file-size", f"File exceeds {limits.max_file_bytes} bytes.", path
            )
            continue
        key = path_key(path)
        if key in accepted:
            total -= len(accepted[key].content.encode())
            message = (
                "Later duplicate replaced earlier."
                if accepted[key].path == path
                else f"Replaced {accepted[key].path!r}, which differs only in letter case."
            )
            collector.add("warning", "duplicate-path", message, path)
        elif len(accepted) >= limits.max_files:
            collector.add("error", "file-count", f"More than {limits.max_files} files; truncated.")
            break
        if total + size > limits.max_total_bytes:
            collector.add("error", "total-size", "Project exceeds the total size limit.", path)
            break
        accepted[key] = file.model_copy(update={"path": path})
        total += size
    return list(accepted.values()), collector.issues


# --- per-format checks ---------------------------------------------------------------------------


def _check_python(path: str, content: str, out: _Collector) -> None:
    try:
        ast.parse(content, filename=path)
    except SyntaxError as exc:
        out.add("error", "python-syntax", f"SyntaxError: {exc.msg}", path, exc.lineno)


def _check_json(path: str, content: str, out: _Collector) -> None:
    try:
        json.loads(content)
    except json.JSONDecodeError as exc:
        out.add("error", "json-syntax", exc.msg, path, exc.lineno)


def _check_toml(path: str, content: str, out: _Collector) -> None:
    try:
        tomllib.loads(content)
    except tomllib.TOMLDecodeError as exc:
        out.add("error", "toml-syntax", str(exc), path)


def _check_k8s_document(path: str, doc: dict[str, Any], out: _Collector) -> None:
    kind = doc.get("kind")
    if kind == "Kustomization":
        return
    for key in ("apiVersion", "kind"):
        if not doc.get(key):
            out.add("error", "k8s-schema", f"Manifest is missing '{key}'.", path)
    if not (doc.get("metadata") or {}).get("name"):
        out.add("error", "k8s-schema", "Manifest is missing 'metadata.name'.", path)
    if kind not in {"Deployment", "StatefulSet", "DaemonSet"}:
        return
    pod = ((doc.get("spec") or {}).get("template") or {}).get("spec") or {}
    containers = pod.get("containers") or []
    if not containers:
        out.add("error", "k8s-schema", f"{kind} has no containers.", path)
    pod_non_root = (pod.get("securityContext") or {}).get("runAsNonRoot") is True
    for container in containers:
        name = container.get("name", "?")
        if not container.get("image"):
            out.add("error", "k8s-schema", f"Container '{name}' has no image.", path)
        if not (container.get("resources") or {}).get("limits"):
            out.add("warning", "k8s-resources", f"Container '{name}' has no resource limits.", path)
        if not container.get("readinessProbe") or not container.get("livenessProbe"):
            out.add("warning", "k8s-probes", f"Container '{name}' lacks liveness/readiness.", path)
        ctx = container.get("securityContext") or {}
        if not (pod_non_root or ctx.get("runAsNonRoot") is True):
            out.add("warning", "k8s-non-root", f"Container '{name}' may run as root.", path)
        if ctx.get("privileged") is True:
            out.add("error", "k8s-privileged", f"Container '{name}' is privileged.", path)


def _check_workflow(path: str, doc: dict[Any, Any], out: _Collector) -> None:
    # PyYAML (YAML 1.1) parses the bare key `on` as boolean True.
    if "on" not in doc and True not in doc:
        out.add("error", "gha-schema", "Workflow has no 'on' trigger.", path)
    jobs = doc.get("jobs")
    if not isinstance(jobs, dict) or not jobs:
        out.add("error", "gha-schema", "Workflow defines no jobs.", path)
        return
    for job_id, job in jobs.items():
        if not isinstance(job, dict):
            out.add("error", "gha-schema", f"Job '{job_id}' is not a mapping.", path)
            continue
        if "uses" in job:  # reusable workflow call
            continue
        if "runs-on" not in job:
            out.add("error", "gha-schema", f"Job '{job_id}' has no 'runs-on'.", path)
        if not job.get("steps"):
            out.add("error", "gha-schema", f"Job '{job_id}' has no steps.", path)
        for step in job.get("steps") or []:
            uses = step.get("uses", "") if isinstance(step, dict) else ""
            if uses and "@" not in uses and not uses.startswith(("./", "docker://")):
                out.add("warning", "gha-pinning", f"Action '{uses}' is not version-pinned.", path)


def _check_yaml(path: str, content: str, out: _Collector) -> None:
    try:
        documents = [doc for doc in yaml.safe_load_all(content) if doc is not None]
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        out.add(
            "error", "yaml-syntax", str(exc).splitlines()[0], path, mark.line + 1 if mark else None
        )
        return
    if path.startswith(".github/workflows/"):
        for doc in documents:
            if isinstance(doc, dict):
                _check_workflow(path, doc, out)
            else:
                out.add("error", "gha-schema", "Workflow must be a mapping.", path)
        return
    for doc in documents:
        if isinstance(doc, dict) and ("apiVersion" in doc or "kind" in doc):
            _check_k8s_document(path, doc, out)


def _check_terraform(path: str, content: str, out: _Collector) -> None:
    try:
        hcl2.load(io.StringIO(content))
    except Exception as exc:  # python-hcl2 raises lark errors of several types
        out.add(
            "error", "hcl-syntax", f"Terraform does not parse: {str(exc).splitlines()[0]}", path
        )


def _check_dockerfile(path: str, content: str, out: _Collector) -> None:
    instructions = [
        line.strip()
        for line in content.splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    froms = [line for line in instructions if line.upper().startswith("FROM ")]
    if not froms:
        out.add("error", "dockerfile", "Dockerfile has no FROM instruction.", path)
        return
    stages: set[str] = set()
    for line in froms:
        tokens = [token for token in line.split()[1:] if not token.startswith("--")]
        if not tokens:
            out.add("error", "dockerfile", f"Malformed instruction: {line}", path)
            continue
        image = tokens[0]
        if len(tokens) >= 3 and tokens[1].upper() == "AS":
            stages.add(tokens[2].lower())
        if image.lower() in stages or image == "scratch":
            continue
        if image.endswith(":latest") or (":" not in image and "@" not in image):
            out.add("warning", "dockerfile-pin", f"Base image '{image}' is not pinned.", path)
    users = [line.split(maxsplit=1)[1] for line in instructions if line.upper().startswith("USER ")]
    if not users or users[-1].split(":")[0] in {"root", "0"}:
        out.add("warning", "dockerfile-user", "Final stage runs as root.", path)
    if not any(line.upper().startswith("HEALTHCHECK") for line in instructions):
        out.add("info", "dockerfile-healthcheck", "No HEALTHCHECK instruction.", path)


def _check_secrets(path: str, content: str, out: _Collector) -> None:
    for rule, pattern in _SECRET_PATTERNS:
        match = pattern.search(content)
        if match:
            line = content.count("\n", 0, match.start()) + 1
            out.add("error", f"secret-{rule}", "Possible hard-coded credential.", path, line)


_CHECKERS: tuple[tuple[Callable[[str], bool], Callable[[str, str, _Collector], None]], ...] = (
    (lambda p: p.endswith(".py"), _check_python),
    (lambda p: p.endswith(".json"), _check_json),
    (lambda p: p.endswith(".toml"), _check_toml),
    (lambda p: p.endswith((".yml", ".yaml")), _check_yaml),
    (lambda p: p.endswith((".tf", ".tfvars")), _check_terraform),
    (
        lambda p: PurePosixPath(p).name == "Dockerfile" or p.endswith(".Dockerfile"),
        _check_dockerfile,
    ),
)


def _check_structure(paths: set[str], out: _Collector) -> None:
    def has(predicate: Callable[[str], bool]) -> bool:
        return any(predicate(p) for p in paths)

    required: list[tuple[str, Callable[[str], bool], Severity]] = [
        ("README.md", lambda p: p == "README.md", "warning"),
        ("a dependency manifest", lambda p: p in {"pyproject.toml", "requirements.txt"}, "error"),
        ("a Dockerfile", lambda p: PurePosixPath(p).name == "Dockerfile", "error"),
        (
            "tests",
            lambda p: PurePosixPath(p).name.startswith("test_") and p.endswith(".py"),
            "error",
        ),
        ("a CI workflow", lambda p: p.startswith(".github/workflows/"), "error"),
        ("Kubernetes manifests", lambda p: p.startswith("deploy/k8s/"), "warning"),
        ("Terraform", lambda p: p.endswith(".tf"), "warning"),
    ]
    for label, predicate, severity in required:
        if not has(predicate):
            out.add(severity, "structure", f"Project is missing {label}.")


def _check_traceability(
    files: dict[str, str], architecture: ArchitectureSpec, out: _Collector
) -> None:
    python_sources = "\n".join(c for p, c in files.items() if p.endswith(".py"))
    for endpoint in architecture.endpoints:
        static = next((seg for seg in endpoint.path.split("/") if seg and "{" not in seg), None)
        if static and f"/{static}" not in python_sources:
            out.add(
                "warning",
                "traceability",
                f"Designed endpoint {endpoint.method} {endpoint.path} not found in the code.",
            )


def validate_project(
    files: list[GeneratedFile], architecture: ArchitectureSpec | None = None
) -> ValidationReport:
    out = _Collector()
    contents = {f.path: f.content for f in files}
    for path, content in contents.items():
        for matches, check in _CHECKERS:
            if matches(path):
                check(path, content, out)
        _check_secrets(path, content, out)
    _check_structure(set(contents), out)
    if architecture is not None:
        _check_traceability(contents, architecture, out)
    return ValidationReport(files_checked=len(contents), issues=out.issues)
