"""Eval fixtures: a spec plus what a correct pipeline must produce for it.

Expectations are written from the spec, not from any provider's output, so a fixture can (and
the clinic fixture does) measure a real shortcoming. Each one is checkable without judgement:
named entities and fields, and HTTP endpoints the generated code must serve.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator


class Expectations(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entities: list[str] = Field(default_factory=list, description="Must appear (by name)")
    # Entities a correct reading of the spec must not invent (e.g. the verb "book" as a Book).
    spurious_entities: list[str] = Field(default_factory=list)
    fields: dict[str, list[str]] = Field(default_factory=dict, description="Entity -> fields")
    endpoints: list[str] = Field(default_factory=list, description='"METHOD /path" entries')
    min_tests: int = Field(default=1, ge=0)

    @field_validator("endpoints")
    @classmethod
    def _endpoint_shape(cls, values: list[str]) -> list[str]:
        for value in values:
            method, _, path = value.partition(" ")
            if method.upper() not in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
                raise ValueError(f"endpoint {value!r} must start with an HTTP method")
            if not path.startswith("/"):
                raise ValueError(f"endpoint {value!r} needs a path starting with /")
        return values


class EvalFixture(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(pattern=r"^[a-z0-9-]{1,40}$")
    description: str = ""
    spec: str = Field(min_length=20)
    expected: Expectations = Field(default_factory=Expectations)


def load_fixtures(directory: Path, only: list[str] | None = None) -> list[EvalFixture]:
    fixtures = []
    for path in sorted(directory.glob("*.yaml")):
        fixture = EvalFixture.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
        if fixture.name != path.stem:
            raise ValueError(f"{path}: fixture name {fixture.name!r} must match the file name")
        fixtures.append(fixture)
    if only:
        unknown = set(only) - {f.name for f in fixtures}
        if unknown:
            raise ValueError(f"unknown fixture(s): {sorted(unknown)}")
        fixtures = [f for f in fixtures if f.name in only]
    if not fixtures:
        raise ValueError(f"no fixtures in {directory}")
    return fixtures
