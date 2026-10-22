"""The eval harness: route extraction, scoring, recordings, the gate, the report and the CLI."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import pytest
import yaml

from autoswe.__main__ import main
from autoswe.evals.fixtures import EvalFixture, load_fixtures
from autoswe.evals.harness import (
    SuiteRun,
    load_recordings,
    record_suite,
    run_fixture,
    run_suite,
    write_report,
)
from autoswe.evals.recording import (
    PROVENANCE,
    Recording,
    RecordingMismatchError,
    ReplayProvider,
)
from autoswe.evals.routes import implemented_endpoints, normalize_endpoint, recall
from autoswe.evals.scoring import METRICS, FixtureResult, Thresholds, check, suite_means
from autoswe.llm.base import LLMRequest
from autoswe.llm.offline import OfflineProvider
from autoswe.pipeline.artifacts import Requirements
from autoswe.sandbox.backends import LocalProcessSandbox
from autoswe.sandbox.models import SandboxLimits

REPO_EVALS = Path(__file__).resolve().parents[1] / "evals"

LIBRARY = EvalFixture(
    name="library",
    spec="A REST API for a small library where members borrow books and librarians track loans.",
    expected={
        "entities": ["Member", "Book", "Loan", "Shelf"],
        "spurious_entities": ["Author"],
        "fields": {"Book": ["title", "colour"]},
        "endpoints": ["GET /books", "POST /loans", "GET /shelves", "PATCH /members/{id}"],
        "min_tests": 1,
    },
)


# --- routes -------------------------------------------------------------------------------------


def test_routes_are_read_from_routers_prefixes_and_mounts() -> None:
    files = {
        "app/main.py": (
            "from fastapi import FastAPI\n"
            "from app.routers import books\n"
            "from app.routers.loans import router as loan_router\n"
            "app = FastAPI()\n"
            "app.include_router(books.router)\n"
            "app.include_router(loan_router, prefix='/v1')\n"
            "@app.get('/healthz')\ndef health(): ...\n"
        ),
        "app/routers/books.py": (
            "from fastapi import APIRouter\nrouter = APIRouter(prefix='/books')\n"
            "@router.get('')\ndef list_books(): ...\n"
            "@router.patch('/{book_id}')\nasync def update(book_id: int): ...\n"
        ),
        "app/routers/loans.py": (
            "from fastapi import APIRouter\nrouter = APIRouter(prefix='/loans')\n"
            "@router.post('/')\ndef create(): ...\n"
        ),
        "app/routers/unused.py": (
            "from fastapi import APIRouter\nrouter = APIRouter(prefix='/ghost')\n"
            "@router.get('')\ndef ghost(): ...\n"
        ),
        "app/broken.py": "def (:\n",
        "README.md": "@router.get('/nope')",
    }
    assert implemented_endpoints(files) == {
        "GET /healthz",
        "GET /books",
        "PATCH /books/{}",
        "POST /v1/loans",  # the mount's own prefix applies
    }  # an unmounted router serves nothing


def test_endpoint_normalisation() -> None:
    assert normalize_endpoint("get", "/books/{book_id}/") == "GET /books/{}"
    assert normalize_endpoint("DELETE", "//a//{x}/b/{y}") == "DELETE /a/{}/b/{}"
    assert recall(["GET /books/{id}"], {"GET /books/{}"}) == 1.0
    assert recall([], set()) is None


# --- fixtures -----------------------------------------------------------------------------------


def test_checked_in_fixtures_load() -> None:
    fixtures = load_fixtures(REPO_EVALS / "fixtures")
    assert {f.name for f in fixtures} >= {"library", "inventory", "clinic"}
    assert load_fixtures(REPO_EVALS / "fixtures", ["clinic"])[0].expected.spurious_entities


@pytest.mark.parametrize(
    "document",
    [
        {"name": "x", "spec": "short"},
        {"name": "Bad Name", "spec": "A long enough specification text."},
        {"name": "x", "spec": "A long enough specification text.",
         "expected": {"endpoints": ["FETCH /x"]}},
        {"name": "x", "spec": "A long enough specification text.",
         "expected": {"endpoints": ["GET books"]}},
        {"name": "x", "spec": "A long enough specification text.", "extra": 1},
    ],
)  # fmt: skip
def test_invalid_fixtures_are_rejected(document: dict[str, Any]) -> None:
    with pytest.raises(ValueError):  # noqa: PT011 - pydantic and our checks both raise it
        EvalFixture.model_validate(document)


def test_fixture_loading_errors(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="no fixtures"):
        load_fixtures(tmp_path)
    (tmp_path / "a.yaml").write_text(
        yaml.safe_dump({"name": "b", "spec": "A long enough specification text."})
    )
    with pytest.raises(ValueError, match="must match the file name"):
        load_fixtures(tmp_path)
    with pytest.raises(ValueError, match="unknown fixture"):
        load_fixtures(REPO_EVALS / "fixtures", ["nope"])


# --- recording and replay ------------------------------------------------------------------------


async def _record(tmp_path: Path, fixture: EvalFixture = LIBRARY) -> Recording:
    (path,) = await record_suite(
        [fixture], OfflineProvider(), source="offline-provider", sandbox=None,
        repair_rounds=0, recordings=tmp_path,
    )  # fmt: skip
    return Recording.load(path)


async def test_recording_round_trips_and_says_where_it_came_from(tmp_path: Path) -> None:
    recording = await _record(tmp_path)
    assert recording.source == "offline-provider"
    assert "NOT output from Claude" in recording.provenance
    assert recording.provenance == PROVENANCE["offline-provider"]
    assert recording.sandbox == "none"
    assert [c.agent for c in recording.calls] == [
        "analyst", "architect", "coder", "devops", "reviewer",
    ]  # fmt: skip

    result = await run_fixture(LIBRARY, ReplayProvider(recording), None, 0)
    assert result.error is None
    assert result.model_calls == 5
    assert result.metrics["completed"] == 1.0
    assert result.metrics["validation_passed"] == 1.0
    # Scored against the fixture's expectations, not against the recording:
    assert result.metrics["entity_recall"] == 0.75  # no Shelf
    assert result.missing_entities == ["Shelf"]
    assert result.metrics["field_recall"] == 0.5  # no colour
    assert result.metrics["no_spurious_entities"] == 1.0
    assert result.metrics["endpoint_recall"] == 0.75
    assert result.missing_endpoints == ["GET /shelves"]
    assert result.metrics["design_coverage"] == 1.0
    for name in ("tests_passed", "test_pass_rate", "lint_clean", "typecheck_clean"):
        assert result.metrics[name] is None  # no sandbox: not measured, not passed


async def test_replay_refuses_a_changed_prompt(tmp_path: Path) -> None:
    recording = await _record(tmp_path)
    changed = LIBRARY.model_copy(update={"spec": LIBRARY.spec + " Loans have due dates."})
    result = await run_fixture(changed, ReplayProvider(recording), None, 0)
    assert result.error is not None and "RecordingMismatchError" in result.error
    assert "different prompt or schema" in result.error
    assert result.metrics["completed"] == 0.0
    assert result.metrics["endpoint_recall"] == 0.0


async def test_replay_refuses_missing_extra_or_reordered_calls(tmp_path: Path) -> None:
    recording = await _record(tmp_path)
    short = recording.model_copy(update={"calls": recording.calls[:3]})
    result = await run_fixture(LIBRARY, ReplayProvider(short), None, 0)
    assert result.error is not None and "has no call #4" in result.error

    extra = recording.model_copy(update={"calls": [*recording.calls, recording.calls[0]]})
    result = await run_fixture(LIBRARY, ReplayProvider(extra), None, 0)
    assert result.error is not None and "never made" in result.error

    swapped = recording.model_copy(update={"calls": recording.calls[1:]})
    replay = ReplayProvider(swapped)
    request = LLMRequest(agent="analyst", system="s", prompt="p", output_type=Requirements)
    with pytest.raises(RecordingMismatchError, match="is architect"):
        await replay.generate(request)


def test_recording_format_is_checked(tmp_path: Path) -> None:
    path = tmp_path / "x.json"
    path.write_text(json.dumps({"format_version": 99, "fixture": "x", "source": "anthropic",
                                "provenance": "p", "model": "m",
                                "recorded_at": "2026-10-23T00:00:00Z", "autoswe_version": "0",
                                "sandbox": "none", "calls": []}))  # fmt: skip
    with pytest.raises(ValueError, match="unsupported recording format"):
        Recording.load(path)
    with pytest.raises(FileNotFoundError, match="autoswe eval record"):
        load_recordings(tmp_path, [LIBRARY])


async def test_checked_in_recordings_replay_against_the_current_code() -> None:
    """Fails when a prompt, schema or offline template changes without re-recording.

    The recordings were made with type checking on, so the sandbox here must match (the
    reviewer's prompt includes the sandbox's verdicts)."""
    fixtures = load_fixtures(REPO_EVALS / "fixtures", ["expenses"])
    recordings = load_recordings(REPO_EVALS / "recordings", fixtures)
    sandbox = LocalProcessSandbox(SandboxLimits(timeout_s=90, typecheck=True))
    run = await run_suite(
        fixtures, lambda f: ReplayProvider(recordings[f.name]), sandbox=sandbox,
        repair_rounds=2, mode="replay", source="offline-provider", provenance="",
    )  # fmt: skip
    (result,) = run.results
    assert result.error is None, result.error
    assert result.metrics["tests_passed"] == 1.0
    assert result.metrics["typecheck_clean"] == 1.0


# --- gate and report ----------------------------------------------------------------------------


def _result(name: str, **metrics: float | None) -> FixtureResult:
    values: dict[str, float | None] = dict.fromkeys(METRICS, 1.0)
    values.update(metrics)
    return FixtureResult(name=name, metrics=values)


def test_means_skip_unmeasured_values_and_the_gate_names_regressions() -> None:
    results = [_result("a", endpoint_recall=0.5), _result("b", tests_passed=None)]
    means = suite_means(results)
    assert means["endpoint_recall"] == 0.75
    assert means["tests_passed"] == 1.0  # one measured value
    thresholds = Thresholds(metrics={"endpoint_recall": 0.9, "tests_passed": 1.0})
    assert check(means, thresholds) == ["endpoint_recall: 0.750 is below the threshold 0.900"]
    unmeasured = suite_means([_result("c", lint_clean=None)])
    failures = check(unmeasured, Thresholds(metrics={"lint_clean": 1.0}))
    assert failures == ["lint_clean: not measured (needs a sandbox: --sandbox docker or local); "
                        "threshold 1.000"]  # fmt: skip


@pytest.mark.parametrize("metrics", [{"nonsense": 1.0}, {"completed": 1.5}])
def test_thresholds_are_validated(metrics: dict[str, float]) -> None:
    with pytest.raises(ValueError):  # noqa: PT011
        Thresholds(metrics=metrics)


def test_checked_in_thresholds_are_valid() -> None:
    thresholds = Thresholds.load(REPO_EVALS / "thresholds.yaml")
    assert thresholds.metrics["tests_passed"] == 1.0


def test_report_files(tmp_path: Path) -> None:
    from datetime import UTC, datetime

    run = SuiteRun(
        mode="replay", source="offline-provider", provenance="Synthesized.", model="m",
        sandbox="docker", sandbox_isolation="container", started_at=datetime.now(UTC),
        duration_s=1.0,
        results=[_result("ok"), _result("weak", endpoint_recall=0.2).model_copy(
            update={"missing_endpoints": ["GET /vets"], "error": "boom"})],
    )  # fmt: skip
    failures = write_report(run, Thresholds(metrics={"endpoint_recall": 0.9}), tmp_path)
    assert failures == ["endpoint_recall: 0.600 is below the threshold 0.900"]
    markdown = (tmp_path / "eval-report.md").read_text(encoding="utf-8")
    assert "**Result: FAIL (1 regression(s))**" in markdown
    assert "**regressed**" in markdown and "missing endpoints: GET /vets" in markdown
    assert "> Synthesized." in markdown and "error: boom" in markdown
    report = json.loads((tmp_path / "eval-report.json").read_text(encoding="utf-8"))
    assert report["passed"] is False and report["means"]["endpoint_recall"] == 0.6
    assert report["fixtures"][1]["missing_endpoints"] == ["GET /vets"]


# --- CLI ----------------------------------------------------------------------------------------


@pytest.fixture
def evals_dir(tmp_path: Path) -> Path:
    root = tmp_path / "evals"
    (root / "fixtures").mkdir(parents=True)
    shutil.copy(REPO_EVALS / "fixtures" / "expenses.yaml", root / "fixtures")
    (root / "thresholds.yaml").write_text(yaml.safe_dump({"metrics": {"completed": 1.0}}))
    return root


def _cli(*args: str) -> int:
    with pytest.raises(SystemExit) as exit_info:
        main(["eval", *args])
    return int(exit_info.value.code or 0)


def test_cli_record_then_run(evals_dir: Path, tmp_path: Path) -> None:
    common = ["--evals-dir", str(evals_dir), "--sandbox", "none"]
    assert _cli("run", *common, "--out", str(tmp_path / "r")) == 2  # nothing recorded yet
    assert _cli("record", *common) == 0
    assert (evals_dir / "recordings" / "expenses.json").is_file()
    assert _cli("run", *common, "--out", str(tmp_path / "r")) == 0
    report = json.loads((tmp_path / "r" / "eval-report.json").read_text(encoding="utf-8"))
    assert report["passed"] and report["source"] == "offline-provider"
    assert report["sandbox"] == "none" and report["means"]["tests_passed"] is None

    (evals_dir / "thresholds.yaml").write_text(yaml.safe_dump({"metrics": {"lint_clean": 1.0}}))
    assert _cli("run", *common, "--out", str(tmp_path / "r")) == 1  # not measured = regression
    assert _cli("run", *common, "--out", str(tmp_path / "r"), "--no-check") == 0


def test_cli_live_and_anthropic_recording_need_a_key(
    evals_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("ANTHROPIC_ENABLED", "false")
    common = ["--evals-dir", str(evals_dir), "--sandbox", "none"]
    assert _cli("run", "--live", *common) == 2
    assert _cli("record", "--source", "anthropic", *common) == 2


def test_cli_rejects_bad_inputs(evals_dir: Path) -> None:
    assert _cli("run", "--evals-dir", str(evals_dir), "--only", "nope") == 2
    (evals_dir / "thresholds.yaml").write_text("metrics: {made_up: 1}")
    assert _cli("run", "--evals-dir", str(evals_dir), "--sandbox", "none") == 2
