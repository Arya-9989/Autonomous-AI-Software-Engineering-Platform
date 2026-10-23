"""Typed artifacts exchanged between pipeline agents.

These models double as the JSON schemas the LLM must fill in, so they deliberately avoid
validation keywords that structured-output schemas do not support (min/max lengths, patterns).
Semantic checks happen afterwards in :mod:`autoswe.pipeline.validation`.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

FieldType = Literal["string", "text", "integer", "float", "boolean", "date", "datetime"]
HttpMethod = Literal["GET", "POST", "PUT", "PATCH", "DELETE"]
Severity = Literal["error", "warning", "info"]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


# --- requirements analyst ----------------------------------------------------------------------


class EntityField(_Model):
    name: str = Field(description="snake_case attribute name")
    type: FieldType
    required: bool = True
    description: str = ""


class Entity(_Model):
    name: str = Field(description="PascalCase singular entity name, e.g. 'Book'")
    description: str
    fields: list[EntityField]


class Requirements(_Model):
    project_name: str = Field(description="kebab-case name for the generated repository")
    summary: str
    user_stories: list[str]
    functional_requirements: list[str]
    non_functional_requirements: list[str]
    entities: list[Entity]
    assumptions: list[str]
    out_of_scope: list[str]


# --- architect -----------------------------------------------------------------------------------


class Component(_Model):
    name: str
    responsibility: str
    technology: str
    depends_on: list[str]


class Endpoint(_Model):
    method: HttpMethod
    path: str = Field(description="URL path, e.g. '/books/{book_id}'")
    summary: str
    entity: str | None = None


class TechChoice(_Model):
    layer: str
    choice: str
    rationale: str


class DecisionRecord(_Model):
    title: str
    decision: str
    rationale: str
    alternatives: list[str]


class DeploymentPlan(_Model):
    container_port: int
    replicas: int
    database: Literal["sqlite", "postgres", "memory"]
    cloud_target: str = Field(description="Where the Terraform targets, e.g. 'aws-ecs-fargate'")
    environment_variables: list[str]


class ArchitectureSpec(_Model):
    style: str
    overview: str
    components: list[Component]
    entities: list[Entity]
    endpoints: list[Endpoint]
    tech_stack: list[TechChoice]
    deployment: DeploymentPlan
    decisions: list[DecisionRecord]


# --- code generator / devops ----------------------------------------------------------------------


class GeneratedFile(_Model):
    path: str = Field(description="POSIX path relative to the repository root")
    content: str
    description: str = ""


class FileBundle(_Model):
    files: list[GeneratedFile]
    notes: str = ""


# --- reviewer -------------------------------------------------------------------------------------


class ReviewFinding(_Model):
    severity: Severity
    file: str | None = None
    message: str
    suggestion: str = ""


class ReviewReport(_Model):
    summary: str
    score: int = Field(description="Overall quality score from 0 to 100")
    findings: list[ReviewFinding]


class RepairRequest(_Model):
    """Context the code generator receives when validation found errors."""

    files: list[GeneratedFile]
    problems: list[str]


# --- validation (deterministic, not LLM-produced) ------------------------------------------------


class ValidationIssue(_Model):
    severity: Severity
    rule: str
    message: str
    file: str | None = None
    line: int | None = None


class ValidationReport(_Model):
    files_checked: int
    issues: list[ValidationIssue]

    @property
    def errors(self) -> list[ValidationIssue]:
        return [issue for issue in self.issues if issue.severity == "error"]

    @property
    def passed(self) -> bool:
        return not self.errors


class StageName(StrEnum):
    ANALYZE = "analyze"
    ARCHITECT = "architect"
    IMPLEMENT = "implement"
    DEVOPS = "devops"
    REVIEW = "review"


STAGE_ORDER: tuple[StageName, ...] = tuple(StageName)

STAGE_LABELS: dict[StageName, str] = {
    StageName.ANALYZE: "Requirements analyst",
    StageName.ARCHITECT: "Architect",
    StageName.IMPLEMENT: "Code generator",
    StageName.DEVOPS: "DevOps engineer",
    StageName.REVIEW: "Reviewer",
}
