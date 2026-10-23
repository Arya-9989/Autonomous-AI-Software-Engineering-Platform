"""TypeScript/Node and Go target stacks: generation, prompts, validation, routes and the API."""

from __future__ import annotations

import asyncio
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from autoswe.__main__ import main
from autoswe.evals.routes import implemented_endpoints_for
from autoswe.llm.offline import OfflineProvider
from autoswe.pipeline import prompts
from autoswe.pipeline.agents import PipelineState, run_pipeline
from autoswe.pipeline.artifacts import GeneratedFile
from autoswe.pipeline.validation import validate_project
from autoswe.stacks import PROFILES, Stack, profile
from tests.conftest import ALICE, LIBRARY_SPEC, HarnessFactory

INVENTORY = (
    "Inventory service. Entities: Product(name, price:float, stock:int, active:bool), "
    "Shipment(carrier, shipped_at:datetime, delivered_on?:date, notes?)"
)


async def generate(stack: Stack, spec: str = LIBRARY_SPEC) -> PipelineState:
    state = PipelineState(spec=spec, stack=stack)

    async def quiet(_: str) -> None:
        return None

    await run_pipeline(state, OfflineProvider(), quiet)
    return state


def write(state: PipelineState, root: Path) -> Path:
    for file in state.files.values():
        target = root / file.path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(file.content, encoding="utf-8", newline="\n")
    return root


@pytest.mark.parametrize(
    ("stack", "expected"),
    [
        (Stack.TYPESCRIPT, {"package.json", "tsconfig.json", "eslint.config.js", "src/app.ts",
                            "src/server.ts", "test/books.test.ts", "Dockerfile",
                            ".github/workflows/ci.yml", "deploy/k8s/deployment.yaml"}),
        (Stack.GO, {"go.mod", "main.go", "handlers.go", "store.go", "books_test.go",
                    "Dockerfile", ".github/workflows/ci.yml", "deploy/terraform/main.tf"}),
    ],
)  # fmt: skip
async def test_offline_pipeline_generates_a_valid_project_per_stack(
    stack: Stack, expected: set[str]
) -> None:
    state = await generate(stack)
    assert expected <= set(state.files)
    assert not any(path.endswith(".py") for path in state.files)
    assert state.validation is not None and state.validation.issues == []
    assert state.architecture is not None
    assert state.architecture.deployment.database == "memory"
    assert state.architecture.deployment.replicas == 1  # in-memory state: one replica, honestly
    assert state.review is not None and state.review.score == 100
    assert any("in memory" in f.message for f in state.review.findings)
    assert not any("create_all" in f.message for f in state.review.findings)


async def test_python_output_is_unchanged_by_the_stack_option() -> None:
    default = PipelineState(spec=LIBRARY_SPEC)
    explicit = await generate(Stack.PYTHON)

    async def quiet(_: str) -> None:
        return None

    await run_pipeline(default, OfflineProvider(), quiet)
    assert {p: f.content for p, f in default.files.items()} == {
        p: f.content for p, f in explicit.files.items()
    }


@pytest.mark.parametrize("stack", [Stack.TYPESCRIPT, Stack.GO])
def test_each_stack_has_its_own_system_prompts(stack: Stack) -> None:
    for role in ("analyst", "architect", "coder", "repair", "test_repair", "devops", "reviewer"):
        text = prompts.system(role, stack)
        assert "Python" not in text and "pytest" not in text and "FastAPI" not in text, role
        assert text == prompts.system(role, stack.value)  # stable: a cacheable prefix
    assert "go 1.27" in prompts.system("coder", Stack.GO).lower()
    assert prompts.system("coder", Stack.PYTHON) == prompts.CODER


@pytest.mark.parametrize(
    ("stack", "missing", "message"),
    [
        (Stack.TYPESCRIPT, "package.json", "a dependency manifest"),
        (Stack.GO, "go.mod", "a dependency manifest"),
        (Stack.GO, "books_test.go", None),
    ],
)
async def test_structure_checks_follow_the_stack(
    stack: Stack, missing: str, message: str | None
) -> None:
    state = await generate(stack)
    files = [f for f in state.files.values() if f.path != missing]
    report = validate_project(files, state.architecture, stack)
    if message is not None:
        assert any(message in issue.message for issue in report.errors)
    # A Python manifest does not satisfy a Go project.
    with_pyproject = [*files, GeneratedFile(path="pyproject.toml", content="[project]\n")]
    if missing == "go.mod":
        assert not validate_project(with_pyproject, state.architecture, stack).passed


async def test_traceability_reads_the_stacks_sources() -> None:
    state = await generate(Stack.GO)
    report = validate_project(list(state.files.values()), state.architecture, Stack.GO)
    assert not any(i.rule == "traceability" for i in report.issues)
    # Without the handlers (and the tests, which name the paths too) nothing serves /books.
    stripped = [
        f
        for f in state.files.values()
        if f.path != "handlers.go" and not f.path.endswith("_test.go")
    ]
    report = validate_project(stripped, state.architecture, Stack.GO)
    assert any(i.rule == "traceability" for i in report.issues)


@pytest.mark.parametrize("stack", [Stack.TYPESCRIPT, Stack.GO])
async def test_routes_are_read_from_typescript_and_go_sources(stack: Stack) -> None:
    state = await generate(stack, INVENTORY)
    files = {path: f.content for path, f in state.files.items()}
    served = implemented_endpoints_for(files, stack.value)
    assert {"GET /healthz", "GET /products", "POST /products", "GET /products/{}",
            "PATCH /shipments/{}", "DELETE /shipments/{}"} <= served  # fmt: skip


def test_express_style_routes_are_understood() -> None:
    files = {"src/routes.ts": 'router.get("/books/:bookId", h);\napp.post(`/loans`, h);',
             "test/x.test.ts": 'app.get("/ignored", h)'}  # fmt: skip
    assert implemented_endpoints_for(files, "typescript") == {"GET /books/{}", "POST /loans"}


def test_stack_profiles() -> None:
    assert profile("go").is_test("books_test.go") and not profile("go").is_test("books.go")
    assert profile(Stack.TYPESCRIPT).is_test("test/a.test.ts")
    assert set(PROFILES) == set(Stack)


@pytest.mark.skipif(shutil.which("node") is None, reason="needs Node.js 24")
async def test_generated_typescript_tests_pass_under_node(tmp_path: Path) -> None:
    root = write(await generate(Stack.TYPESCRIPT, INVENTORY), tmp_path)
    run = await asyncio.to_thread(
        subprocess.run, ["node", "--test", "test/*.test.ts"],
        cwd=root, capture_output=True, text=True, check=False, timeout=120,
    )  # fmt: skip
    assert run.returncode == 0, run.stdout + run.stderr
    assert "pass 13" in run.stdout


async def test_jobs_api_accepts_a_stack(harness_factory: HarnessFactory) -> None:
    harness = await harness_factory()
    job = await harness.submit(stack="go")
    detail = await harness.wait_for(job["id"], {"succeeded"})
    assert detail["stack"] == "go"
    files = (await harness.client.get(f"/api/v1/jobs/{job['id']}/files", headers=ALICE)).json()
    assert "go.mod" in {f["path"] for f in files}
    bad = await harness.client.post(
        "/api/v1/jobs", json={"spec": LIBRARY_SPEC, "stack": "cobol"}, headers=ALICE
    )
    assert bad.status_code == 422
    listed = (await harness.client.get("/api/v1/jobs", headers=ALICE)).json()["items"]
    assert listed[0]["stack"] == "go"


def test_cli_generates_a_typescript_project(tmp_path: Path) -> None:
    out = tmp_path / "ts"
    with pytest.raises(SystemExit) as exit_info:
        main(["generate", INVENTORY, "--out", str(out), "--provider", "offline",
              "--stack", "typescript", "--sandbox", "none"])  # fmt: skip
    assert exit_info.value.code == 0
    assert (out / "src" / "app.ts").is_file() and (out / "package.json").is_file()


def _gofmt_clean(content: str) -> bool:
    """Every line of a struct is aligned the way gofmt does: no tab after the first column."""
    return all("\t" not in line.lstrip("\t") for line in content.splitlines())


async def test_go_sources_use_gofmt_alignment() -> None:
    state = await generate(Stack.GO, INVENTORY)
    for path, file in state.files.items():
        if path.endswith(".go"):
            assert _gofmt_clean(file.content), path
    models = state.files["models.go"].content
    assert '\tID          int64   `json:"id"`' in models  # columns padded with spaces


def run_args_of(stack: Stack) -> list[str]:
    from autoswe.sandbox.backends import DockerSandbox
    from autoswe.sandbox.models import SandboxLimits

    images = {Stack.GO: "go:1", Stack.TYPESCRIPT: "node:1"}
    sandbox = DockerSandbox("py:1", SandboxLimits(), images=images)
    return sandbox.run_args("c", stack)


@pytest.mark.parametrize(("stack", "image"), [(Stack.TYPESCRIPT, "node:1"), (Stack.GO, "go:1")])
def test_each_stack_runs_in_its_own_image(stack: Stack, image: str) -> None:
    args = run_args_of(stack)
    assert image in args and args[args.index("--stack") + 1] == stack.value
    tmp = next(arg for arg in args if arg.startswith("/tmp:"))
    # Only Go needs to execute the test binary it compiles under /tmp.
    assert ("noexec" in tmp) is (stack is not Stack.GO)
    assert all("noexec" in arg for arg in args if arg.startswith("/workspace:"))


def test_go_runs_get_room_for_the_build_cache() -> None:
    args: dict[str, Any] = {}
    go = run_args_of(Stack.GO)
    args["memory"] = go[go.index("--memory") + 1]
    assert args["memory"] == "1024m"
    python = run_args_of(Stack.PYTHON)
    assert python[python.index("--memory") + 1] == "768m"
