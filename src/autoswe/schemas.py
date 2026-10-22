"""HTTP API request/response models."""

from __future__ import annotations

from datetime import datetime
from pathlib import PurePosixPath
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from autoswe.models import Job, JobStatus, StageRun, StageStatus
from autoswe.pipeline.artifacts import STAGE_LABELS, StageName

MIN_SPEC_CHARS = 20
MAX_SPEC_CHARS = 20_000


class JobCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    spec: str = Field(
        min_length=MIN_SPEC_CHARS,
        max_length=MAX_SPEC_CHARS,
        description="Natural-language description of the software to build.",
        examples=[
            "A REST API for a small library: members borrow books, librarians track loans "
            "and due dates."
        ],
    )
    title: str | None = Field(default=None, max_length=120)
    provider: str | None = Field(default=None, description="Defaults to the server default.")

    @field_validator("spec")
    @classmethod
    def _strip_spec(cls, value: str) -> str:
        stripped = value.strip()
        if len(stripped) < MIN_SPEC_CHARS:
            raise ValueError(f"spec must contain at least {MIN_SPEC_CHARS} non-blank characters")
        return stripped

    @field_validator("title")
    @classmethod
    def _strip_title(cls, value: str | None) -> str | None:
        return value.strip() or None if value is not None else None


class StageOut(BaseModel):
    name: StageName
    label: str
    status: StageStatus
    summary: str | None
    started_at: datetime | None
    finished_at: datetime | None
    duration_s: float | None
    input_tokens: int
    output_tokens: int

    @classmethod
    def from_model(cls, stage: StageRun) -> StageOut:
        duration = None
        if stage.started_at and stage.finished_at:
            duration = round((stage.finished_at - stage.started_at).total_seconds(), 3)
        name = StageName(stage.name)
        return cls(
            name=name,
            label=STAGE_LABELS[name],
            status=stage.status,
            summary=stage.summary,
            started_at=stage.started_at,
            finished_at=stage.finished_at,
            duration_s=duration,
            input_tokens=stage.input_tokens,
            output_tokens=stage.output_tokens,
        )


class JobSummary(BaseModel):
    id: str
    title: str
    status: JobStatus
    provider: str
    model: str | None
    current_stage: StageName | None
    project_name: str | None
    progress: float = Field(description="Fraction of stages finished, 0..1")
    created_at: datetime
    finished_at: datetime | None

    @classmethod
    def from_model(cls, job: Job) -> JobSummary:
        return cls(**_summary_fields(job))


def _summary_fields(job: Job) -> dict[str, Any]:
    done = sum(s.status in {StageStatus.SUCCEEDED, StageStatus.SKIPPED} for s in job.stages)
    return {
        "id": job.id,
        "title": job.title,
        "status": job.status,
        "provider": job.provider,
        "model": job.model,
        "current_stage": StageName(job.current_stage) if job.current_stage else None,
        "project_name": job.project_name,
        "progress": round(done / len(job.stages), 3) if job.stages else 0.0,
        "created_at": job.created_at,
        "finished_at": job.finished_at,
    }


class ValidationSummary(BaseModel):
    passed: bool
    errors: int
    warnings: int
    files_checked: int


class TestsSummary(BaseModel):
    __test__ = False  # not a pytest test class

    status: str
    backend: str
    collected: int
    passed: int
    failed: int
    errors: int
    attempts: int
    repair_budget_exhausted: bool

    @classmethod
    def from_report(cls, report: dict[str, Any]) -> TestsSummary:
        final = report.get("final") or {}
        tests = final.get("tests") or {}
        return cls(
            status=str(final.get("status", "error")),
            backend=str(final.get("backend", "")),
            collected=int(tests.get("collected", 0)),
            passed=int(tests.get("passed", 0)),
            failed=int(tests.get("failed", 0)),
            errors=int(tests.get("errors", 0)),
            attempts=len(report.get("attempts") or []),
            repair_budget_exhausted=bool(report.get("repair_budget_exhausted")),
        )


class JobDetail(JobSummary):
    spec: str
    error: str | None
    started_at: datetime | None
    stages: list[StageOut]
    input_tokens: int
    output_tokens: int
    cancel_requested: bool
    validation: ValidationSummary | None
    tests: TestsSummary | None = Field(
        default=None, description="Sandboxed test run; null when tests were not executed"
    )
    review_score: int | None

    @classmethod
    def from_model(cls, job: Job) -> JobDetail:
        validation = None
        if job.validation:
            issues = job.validation.get("issues", [])
            errors = sum(i.get("severity") == "error" for i in issues)
            validation = ValidationSummary(
                passed=errors == 0,
                errors=errors,
                warnings=sum(i.get("severity") == "warning" for i in issues),
                files_checked=int(job.validation.get("files_checked", 0)),
            )
        return cls(
            **_summary_fields(job),
            spec=job.spec,
            error=job.error,
            started_at=job.started_at,
            stages=[StageOut.from_model(stage) for stage in job.stages],
            input_tokens=job.input_tokens,
            output_tokens=job.output_tokens,
            cancel_requested=job.cancel_requested,
            validation=validation,
            tests=TestsSummary.from_report(job.test_report) if job.test_report else None,
            review_score=(job.review or {}).get("score"),
        )


class JobPage(BaseModel):
    items: list[JobSummary]
    next_cursor: datetime | None
    next_cursor_id: str | None = Field(
        default=None,
        description="Pass as before_id with before, so jobs created in the same "
        "instant as the page's last job are neither skipped nor repeated.",
    )


_LANGUAGES = {
    ".py": "python", ".toml": "toml", ".yml": "yaml", ".yaml": "yaml", ".json": "json",
    ".md": "markdown", ".tf": "hcl", ".tfvars": "hcl", ".sh": "bash", ".ini": "ini",
    ".cfg": "ini", ".txt": "text", ".html": "html", ".css": "css", ".js": "javascript",
    ".ts": "typescript", ".sql": "sql",
}  # fmt: skip


def language_for(path: str) -> str:
    name = PurePosixPath(path).name
    if name == "Dockerfile" or name.endswith(".Dockerfile"):
        return "docker"
    if name in {".dockerignore", ".gitignore"}:
        return "text"
    return _LANGUAGES.get(PurePosixPath(path).suffix.lower(), "text")


class FileEntry(BaseModel):
    path: str
    size: int
    stage: str
    description: str
    language: str


class FileContent(FileEntry):
    content: str


class ProviderOut(BaseModel):
    name: str
    model: str
    default: bool


class ServerInfo(BaseModel):
    version: str
    auth_required: bool
    providers: list[ProviderOut]
    max_spec_chars: int = MAX_SPEC_CHARS
    min_spec_chars: int = MIN_SPEC_CHARS
