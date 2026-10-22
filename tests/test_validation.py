from textwrap import dedent

import pytest

from autoswe.pipeline.artifacts import GeneratedFile
from autoswe.pipeline.validation import Limits, normalize_path, sanitize_files, validate_project

MINIMAL = {
    "README.md": "# demo\n",
    "pyproject.toml": '[project]\nname = "demo"\n',
    "Dockerfile": "FROM python:3.12-slim\nUSER 10001\nHEALTHCHECK CMD true\n",
    "tests/test_app.py": "def test_ok():\n    assert True\n",
    ".github/workflows/ci.yml": dedent("""
        on: [push]
        jobs:
          test:
            runs-on: ubuntu-latest
            steps:
              - uses: actions/checkout@v4
    """),
    "deploy/k8s/service.yaml": dedent("""
        apiVersion: v1
        kind: Service
        metadata:
          name: demo
    """),
    "deploy/terraform/main.tf": 'resource "aws_ecr_repository" "app" {\n  name = "demo"\n}\n',
}


def _files(**overrides: str | None) -> list[GeneratedFile]:
    merged: dict[str, str | None] = {**MINIMAL, **overrides}
    return [GeneratedFile(path=p, content=c) for p, c in merged.items() if c is not None]


def _rules(files: list[GeneratedFile]) -> dict[str, str]:
    return {issue.rule: issue.severity for issue in validate_project(files).issues}


def test_minimal_project_is_clean() -> None:
    report = validate_project(_files())
    assert report.passed
    assert report.issues == []
    assert report.files_checked == len(MINIMAL)


@pytest.mark.parametrize(
    "path",
    ["/etc/passwd", "../escape.py", "app/../../x", "C:/win.py", "app\\win.py", ".git/config",
     "", "a/./b", "a//b", "x\x00y", "/".join(["d"] * 9)],
)  # fmt: skip
def test_unsafe_paths_are_rejected(path: str) -> None:
    assert normalize_path(path) is None


def test_safe_paths_are_normalised() -> None:
    assert normalize_path(" app/main.py ") == "app/main.py"
    assert normalize_path(".github/workflows/ci.yml") == ".github/workflows/ci.yml"


def test_sanitize_enforces_limits_and_dedupes() -> None:
    files = [
        GeneratedFile(path="a.py", content="x = 1\n"),
        GeneratedFile(path="a.py", content="x = 2\n"),
        GeneratedFile(path="../evil.py", content=""),
        GeneratedFile(path="big.txt", content="x" * 50),
        GeneratedFile(path="b.py", content=""),
        GeneratedFile(path="c.py", content=""),
    ]
    kept, issues = sanitize_files(files, Limits(max_files=2, max_file_bytes=20))
    assert [f.path for f in kept] == ["a.py", "b.py"]
    assert kept[0].content == "x = 2\n"
    assert {i.rule for i in issues} == {"duplicate-path", "path-safety", "file-size", "file-count"}


def test_total_size_limit() -> None:
    files = [GeneratedFile(path=f"f{i}.txt", content="x" * 10) for i in range(5)]
    kept, issues = sanitize_files(files, Limits(max_total_bytes=25))
    assert len(kept) == 2
    assert issues[-1].rule == "total-size"


def test_python_syntax_errors_are_errors_with_line_numbers() -> None:
    report = validate_project(_files(**{"app/main.py": "def broken(:\n    pass\n"}))
    (issue,) = report.errors
    assert issue.rule == "python-syntax"
    assert issue.file == "app/main.py"
    assert issue.line == 1


def test_yaml_json_toml_and_hcl_parsers() -> None:
    rules = _rules(
        _files(**{
            "deploy/k8s/bad.yaml": "a: [unclosed\n",
            "docs/x.json": "{not json}",
            "pyproject.toml": "[project\n",
            "deploy/terraform/main.tf": 'resource "x" {\n',
        })
    )  # fmt: skip
    assert rules == {
        "yaml-syntax": "error",
        "json-syntax": "error",
        "toml-syntax": "error",
        "hcl-syntax": "error",
    }


def test_kubernetes_best_practice_checks() -> None:
    deployment = dedent("""
        apiVersion: apps/v1
        kind: Deployment
        metadata:
          name: api
        spec:
          template:
            spec:
              containers:
                - name: api
                  securityContext:
                    privileged: true
    """)
    rules = _rules(_files(**{"deploy/k8s/deployment.yaml": deployment}))
    assert rules["k8s-schema"] == "error"  # no image
    assert rules["k8s-privileged"] == "error"
    assert rules["k8s-resources"] == "warning"
    assert rules["k8s-probes"] == "warning"
    assert rules["k8s-non-root"] == "warning"


def test_kubernetes_manifest_without_name_is_an_error() -> None:
    rules = _rules(_files(**{"deploy/k8s/cm.yaml": "apiVersion: v1\nkind: ConfigMap\n"}))
    assert rules == {"k8s-schema": "error"}


def test_workflow_checks_handle_yaml_on_as_boolean() -> None:
    workflow = "jobs:\n  build:\n    steps:\n      - uses: actions/checkout\n"
    report = validate_project(_files(**{".github/workflows/ci.yml": workflow}))
    messages = {i.message for i in report.issues}
    assert "Workflow has no 'on' trigger." in messages
    assert "Job 'build' has no 'runs-on'." in messages
    assert "Action 'actions/checkout' is not version-pinned." in messages


def test_dockerfile_rules() -> None:
    rules = _rules(_files(Dockerfile="FROM python\nRUN echo hi\n"))
    assert rules == {
        "dockerfile-pin": "warning",
        "dockerfile-user": "warning",
        "dockerfile-healthcheck": "info",
    }
    assert _rules(_files(Dockerfile="RUN echo hi\n")) == {"dockerfile": "error"}


def test_dockerfile_stage_references_are_not_flagged() -> None:
    dockerfile = (
        "FROM python:3.12-slim AS build\nFROM --platform=linux/amd64 build AS final\n"
        "USER app\nHEALTHCHECK CMD true\n"
    )
    assert _rules(_files(Dockerfile=dockerfile)) == {}


@pytest.mark.parametrize(
    "secret",
    [
        "AKIAABCDEFGHIJKLMNOP",
        "-----BEGIN RSA PRIVATE KEY-----",
        "sk-ant-api03-abcdefghijklmnopqrstuvwxyz",
        "ghp_" + "a" * 36,
    ],
)
def test_secret_scanning(secret: str) -> None:
    report = validate_project(_files(**{"app/settings.py": f'KEY = "{secret}"\n'}))
    assert any(i.rule.startswith("secret-") and i.severity == "error" for i in report.issues)


def test_missing_structure_is_reported() -> None:
    report = validate_project(_files(**{"Dockerfile": None, "tests/test_app.py": None}))
    assert {i.message for i in report.errors} == {
        "Project is missing a Dockerfile.",
        "Project is missing tests.",
    }
