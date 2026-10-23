"""Source templates for the FastAPI service the offline provider generates.

The output is a complete, runnable project: CRUD routers per entity, Pydantic schemas,
SQLAlchemy models and a pytest suite that exercises every endpoint. The platform's own test
suite runs the generated tests to prove the output actually works.
"""

from __future__ import annotations

from textwrap import dedent

from autoswe.llm.offline import templates_migrations as migrations
from autoswe.pipeline import relations
from autoswe.pipeline.artifacts import ArchitectureSpec, Entity, EntityField, GeneratedFile
from autoswe.pipeline.naming import kebab, pluralize, snake

_SA_TYPES = {
    "string": ("String(255)", "str"),
    "text": ("Text", "str"),
    "integer": ("Integer", "int"),
    "float": ("Float", "float"),
    "boolean": ("Boolean", "bool"),
    "date": ("Date", "date"),
    "datetime": ("DateTime(timezone=True)", "datetime"),
}
_SAMPLES = {
    "string": ('"example {name}"', '"updated {name}"'),
    "text": ('"Some longer {name} text."', '"Revised {name} text."'),
    "integer": ("3", "7"),
    "float": ("9.5", "12.25"),
    "boolean": ("True", "False"),
    "date": ('"2025-01-15"', '"2025-02-20"'),
    "datetime": ('"2025-01-15T09:30:00Z"', '"2025-02-20T14:00:00Z"'),
}


def _src(text: str) -> str:
    return dedent(text).lstrip("\n")


def _table(entity: Entity) -> str:
    return snake(pluralize(entity.name))


def _py_type(field: EntityField) -> str:
    return _SA_TYPES[field.type][1]


def _sample(field: EntityField, *, updated: bool = False) -> str:
    template = _SAMPLES[field.type][1 if updated else 0]
    return template.replace("{name}", field.name.replace("_", " "))


def render_db() -> str:
    return _src('''
        """Database engine, session factory and declarative base."""

        import os
        from collections.abc import Iterator
        from typing import Any

        from sqlalchemy import create_engine, event
        from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

        DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./app.db")

        _connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
        engine = create_engine(DATABASE_URL, connect_args=_connect_args, pool_pre_ping=True)
        SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

        if DATABASE_URL.startswith("sqlite"):

            @event.listens_for(engine, "connect")
            def _enforce_foreign_keys(connection: Any, _record: Any) -> None:
                # SQLite ignores foreign keys (and ON DELETE CASCADE) unless this is on.
                connection.execute("PRAGMA foreign_keys=ON")


        class Base(DeclarativeBase):
            pass


        def get_session() -> Iterator[Session]:
            with SessionLocal() as session:
                yield session
    ''')


def render_models(entities: list[Entity]) -> str:
    needs_date = any(f.type == "date" for e in entities for f in e.fields)
    known = {e.name for e in entities}
    has_parents = any(parent in known for e in entities for parent in e.belongs_to)
    sa_names = sorted(
        {"DateTime", "Integer"}
        | {_SA_TYPES[f.type][0].split("(")[0] for e in entities for f in e.fields}
        | ({"ForeignKey"} if has_parents else set())
    )
    lines = [
        '"""SQLAlchemy ORM models."""',
        "",
        f"from datetime import {'UTC, date, datetime' if needs_date else 'UTC, datetime'}",
        "",
        f"from sqlalchemy import {', '.join(sa_names)}",
        "from sqlalchemy.orm import Mapped, mapped_column",
        "",
        "from app.db import Base",
        "",
        "",
        "def _utcnow() -> datetime:",
        "    return datetime.now(UTC)",
    ]
    for entity in entities:
        lines += [
            "",
            "",
            f"class {entity.name}(Base):",
            f'    __tablename__ = "{_table(entity)}"',
            "",
        ]
        lines.append("    id: Mapped[int] = mapped_column(Integer, primary_key=True)")
        for field in entity.fields:
            sa_type = _SA_TYPES[field.type][0]
            annotation = _py_type(field) if field.required else f"{_py_type(field)} | None"
            lines.append(
                f"    {field.name}: Mapped[{annotation}] = mapped_column("
                f"{sa_type}, nullable={not field.required})"
            )
        for parent in (p for p in entity.belongs_to if p in known):
            column = migrations.foreign_key(parent)
            target = f'ForeignKey("{migrations.table_name(parent)}.id", ondelete="CASCADE")'
            lines += _call(
                "    ", f"{column}: Mapped[int] = mapped_column", f"{target}, index=True"
            )
        lines.append(
            "    created_at: Mapped[datetime] = mapped_column("
            "DateTime(timezone=True), default=_utcnow)"
        )
    return "\n".join(lines) + "\n"


def _parents(entity: Entity, entities: list[Entity]) -> list[str]:
    known = {e.name for e in entities}
    return [parent for parent in entity.belongs_to if parent in known]


def _not_null(entity: Entity, entities: list[Entity]) -> list[str]:
    """The entity's NOT NULL columns a client may send: required fields and foreign keys."""
    required = [f.name for f in entity.fields if f.required]
    return required + [migrations.foreign_key(parent) for parent in _parents(entity, entities)]


def render_schemas(entities: list[Entity]) -> str:
    needs_date = any(f.type == "date" for e in entities for f in e.fields)
    lines = [
        '"""Pydantic request/response schemas."""',
        "",
        f"from datetime import {'date, datetime' if needs_date else 'datetime'}",
        "",
        "from pydantic import BaseModel, ConfigDict, Field"
        + (", field_validator" if any(_not_null(e, entities) for e in entities) else ""),
    ]
    for entity in entities:
        name = entity.name
        lines += ["", "", f"class {name}Create(BaseModel):"]
        for field in entity.fields:
            constraint = ", max_length=255" if field.type == "string" else ""
            if field.required:
                lines.append(f"    {field.name}: {_py_type(field)} = Field(...{constraint})")
            else:
                lines.append(
                    f"    {field.name}: {_py_type(field)} | None = Field(None{constraint})"
                )
        for parent in _parents(entity, entities):
            lines.append(f"    {migrations.foreign_key(parent)}: int = Field(..., gt=0)")
        lines += ["", "", f"class {name}Update(BaseModel):"]
        for field in entity.fields:
            constraint = ", max_length=255" if field.type == "string" else ""
            lines.append(f"    {field.name}: {_py_type(field)} | None = Field(None{constraint})")
        for parent in _parents(entity, entities):
            lines.append(f"    {migrations.foreign_key(parent)}: int | None = Field(None, gt=0)")
        # Every Update field may be omitted, but a NOT NULL column must not be set to null: that
        # would pass validation and fail in the database as a 500 instead of a 422.
        not_null = _not_null(entity, entities)
        if not_null:
            names = ", ".join(f'"{column}"' for column in not_null)
            lines += [
                "",
                *_call("    ", "@field_validator", names),
                "    @classmethod",
                "    def _not_null(cls, value: object) -> object:",
                "        if value is None:",
                '            raise ValueError("may be omitted, but not set to null")',
                "        return value",
            ]
        lines += [
            "",
            "",
            f"class {name}Read({name}Create):",
            "    model_config = ConfigDict(from_attributes=True)",
            "",
            "    id: int",
            "    created_at: datetime",
        ]
    return "\n".join(lines) + "\n"


def render_router(entity: Entity, entities: list[Entity] | None = None) -> str:
    name = entity.name
    var = snake(name)
    plural = snake(pluralize(name))
    prefix = "/" + kebab(pluralize(name))
    parents = _parents(entity, entities or [entity])
    keys = [migrations.foreign_key(parent) for parent in parents]
    models = ", ".join(sorted({name, *parents}))
    lines = [
        f'"""CRUD endpoints for {plural}."""',
        "",
        "from typing import Annotated",
        "",
        "from fastapi import APIRouter, Depends, HTTPException, Query, Response, status",
        "from sqlalchemy import select",
        "from sqlalchemy.orm import Session",
        "",
        "from app.db import get_session",
        f"from app.models import {models}",
        f"from app.schemas import {name}Create, {name}Read, {name}Update",
        "",
        f'router = APIRouter(prefix="{prefix}", tags=["{plural}"])',
        "SessionDep = Annotated[Session, Depends(get_session)]",
        "",
        "",
        f"def _get_or_404(session: Session, {var}_id: int) -> {name}:",
        f"    {var} = session.get({name}, {var}_id)",
        f"    if {var} is None:",
        f'        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="{name} not found")',
        f"    return {var}",
    ]
    if parents:
        lines += ["", "", "def _check_parents(", "    session: Session,"]
        lines += [f"    {key}: int | None," for key in keys]
        noun = " and ".join(f"{_article(p)} {p.lower()}" for p in parents)
        lines += [") -> None:", f'    """{_article(name).capitalize()} {name.lower()} must '
                  f'point at {noun} that exist."""']  # fmt: skip
        for parent, key in zip(parents, keys, strict=True):
            lines += [
                f"    if {key} is not None and session.get({parent}, {key}) is None:",
                f'        raise HTTPException(422, detail=f"{parent} {{{key}}} not found")',
            ]
    lines += [
        "",
        "",
        f'@router.get("", response_model=list[{name}Read])',
        f"def list_{plural}(",
        "    session: SessionDep,",
        "    limit: Annotated[int, Query(ge=1, le=200)] = 50,",
        "    offset: Annotated[int, Query(ge=0)] = 0,",
        *[f"    {key}: Annotated[int | None, Query(gt=0)] = None," for key in keys],
        f") -> list[{name}]:",
        f"    query = select({name}).order_by({name}.id)",
    ]
    for key in keys:
        lines += [
            f"    if {key} is not None:",
            f"        query = query.where({name}.{key} == {key})",
        ]
    lines += [
        "    return list(session.scalars(query.offset(offset).limit(limit)))",
        "",
        "",
        f'@router.post("", response_model={name}Read, status_code=status.HTTP_201_CREATED)',
        f"def create_{var}(payload: {name}Create, session: SessionDep) -> {name}:",
    ]
    if parents:
        lines.append(
            "    _check_parents(session, " + ", ".join(f"payload.{key}" for key in keys) + ")"
        )
    lines += [
        f"    {var} = {name}(**payload.model_dump())",
        f"    session.add({var})",
        "    session.commit()",
        f"    session.refresh({var})",
        f"    return {var}",
        "",
        "",
        f'@router.get("/{{{var}_id}}", response_model={name}Read)',
        f"def get_{var}({var}_id: int, session: SessionDep) -> {name}:",
        f"    return _get_or_404(session, {var}_id)",
        "",
        "",
        f'@router.patch("/{{{var}_id}}", response_model={name}Read)',
        f"def update_{var}(",
        f"    {var}_id: int,",
        f"    payload: {name}Update,",
        "    session: SessionDep,",
        f") -> {name}:",
        f"    {var} = _get_or_404(session, {var}_id)",
    ]
    if parents:
        lines.append(
            "    _check_parents(session, " + ", ".join(f"payload.{key}" for key in keys) + ")"
        )
    lines += [
        "    for key, value in payload.model_dump(exclude_unset=True).items():",
        f"        setattr({var}, key, value)",
        "    session.commit()",
        f"    session.refresh({var})",
        f"    return {var}",
        "",
        "",
        f'@router.delete("/{{{var}_id}}", status_code=status.HTTP_204_NO_CONTENT)',
        f"def delete_{var}({var}_id: int, session: SessionDep) -> Response:",
        f"    session.delete(_get_or_404(session, {var}_id))",
        "    session.commit()",
        "    return Response(status_code=status.HTTP_204_NO_CONTENT)",
    ]
    return "\n".join(lines) + "\n"


def _article(noun: str) -> str:
    return "an" if noun[:1].lower() in "aeiou" else "a"


def render_main(title: str, entities: list[Entity]) -> str:
    imports = ", ".join(sorted(snake(pluralize(e.name)) for e in entities))
    includes = "\n".join(f"app.include_router({snake(pluralize(e.name))}.router)" for e in entities)
    return (
        _src(f"""
        \"\"\"Application entry point. The schema is managed by Alembic (alembic upgrade head).\"\"\"

        from fastapi import FastAPI

        from app.routers import {imports}

        app = FastAPI(title="{title}", version="0.1.0")
        """)
        + includes
        + "\n\n\n"
        + _src("""
        @app.get("/healthz", tags=["ops"])
        def healthz() -> dict[str, str]:
            return {"status": "ok"}
        """)
    )


def render_conftest() -> str:
    return _src("""
        import os
        import tempfile
        from collections.abc import Iterator
        from pathlib import Path

        import pytest

        _DB_FILE = Path(tempfile.mkdtemp()) / "test.db"
        os.environ["DATABASE_URL"] = f"sqlite:///{_DB_FILE.as_posix()}"

        from alembic import command  # noqa: E402
        from alembic.config import Config  # noqa: E402
        from fastapi.testclient import TestClient  # noqa: E402

        from app.db import Base, engine  # noqa: E402
        from app.main import app  # noqa: E402

        ROOT = Path(__file__).resolve().parents[1]


        @pytest.fixture(scope="session", autouse=True)
        def schema() -> None:
            \"\"\"The test database is built by the project's own migrations.\"\"\"
            config = Config(str(ROOT / "alembic.ini"))
            config.set_main_option("script_location", str(ROOT / "migrations"))
            command.upgrade(config, "head")


        @pytest.fixture
        def client() -> Iterator[TestClient]:
            with engine.begin() as connection:  # children first, so foreign keys hold
                for table in reversed(Base.metadata.sorted_tables):
                    connection.execute(table.delete())
            with TestClient(app) as test_client:
                yield test_client
    """)


def _call(indent: str, head: str, args: str) -> list[str]:
    """``head(args)`` on one line when it fits in 100 columns, else split as ruff format would."""
    line = f"{indent}{head}({args})"
    if len(line) <= 100:
        return [line]
    return [f"{indent}{head}(", f"{indent}    {args}", f"{indent})"]


def _assert_equals(expr: str, field: EntityField, *, updated: bool = False) -> str:
    value = _sample(field, updated=updated)
    operator = "is" if field.type == "boolean" else "=="
    return f'    assert {expr}["{field.name}"] {operator} {value}'


def _render_plain_entity_test(entity: Entity) -> str:
    var = snake(entity.name)
    prefix = "/" + kebab(pluralize(entity.name))
    comparable = [f for f in entity.fields if f.type not in {"date", "datetime"}]
    target = next((f for f in entity.fields if f.type in {"string", "text"}), entity.fields[0])
    item = 'f"' + prefix + "/{created['id']}\""

    lines = ["from fastapi.testclient import TestClient", "", "PAYLOAD = {"]
    lines += [f'    "{f.name}": {_sample(f)},' for f in entity.fields]
    lines += ["}", "", ""]
    lines += [
        f"def test_create_and_get_{var}(client: TestClient) -> None:",
        f'    response = client.post("{prefix}", json=PAYLOAD)',
        "    assert response.status_code == 201, response.text",
        "    created = response.json()",
        *[_assert_equals("created", f) for f in comparable],
        f"    fetched = client.get({item})",
        "    assert fetched.status_code == 200",
        '    assert fetched.json()["id"] == created["id"]',
        "",
        "",
        f"def test_list_{var}_supports_pagination(client: TestClient) -> None:",
        "    for _ in range(3):",
        f'        assert client.post("{prefix}", json=PAYLOAD).status_code == 201',
        f'    page = client.get("{prefix}", params={{"limit": 2, "offset": 1}})',
        "    assert page.status_code == 200",
        "    assert len(page.json()) == 2",
        "",
        "",
        f"def test_update_{var}(client: TestClient) -> None:",
        f'    created = client.post("{prefix}", json=PAYLOAD).json()',
        *_call(
            "    ",
            "response = client.patch",
            f'{item}, json={{"{target.name}": {_sample(target, updated=True)}}}',
        ),
        "    assert response.status_code == 200",
    ]
    if target.type not in {"date", "datetime"}:
        lines.append(_assert_equals("response.json()", target, updated=True))
    lines += [
        "",
        "",
        f"def test_delete_{var}(client: TestClient) -> None:",
        f'    created = client.post("{prefix}", json=PAYLOAD).json()',
        f"    assert client.delete({item}).status_code == 204",
        f"    assert client.get({item}).status_code == 404",
        "",
        "",
        f"def test_unknown_{var}_returns_404(client: TestClient) -> None:",
        f'    assert client.get("{prefix}/999999").status_code == 404',
    ]
    required = next((f for f in entity.fields if f.required), None)
    if required is not None:
        lines += [
            "",
            "",
            f"def test_create_{var}_rejects_missing_required_fields(client: TestClient) -> None:",
            f'    assert client.post("{prefix}", json={{}}).status_code == 422',
            "",
            "",
            f"def test_update_{var}_rejects_null_required_field(client: TestClient) -> None:",
            f'    created = client.post("{prefix}", json=PAYLOAD).json()',
            *_call("    ", "response = client.patch", f'{item}, json={{"{required.name}": None}}'),
            "    assert response.status_code == 422",
        ]
    return "\n".join(lines) + "\n"


def _ancestors(entity: Entity, by_name: dict[str, Entity]) -> list[Entity]:
    """Every entity ``entity`` needs created first, parents of parents before their children."""
    ordered: list[Entity] = []

    def visit(name: str) -> None:
        for parent in by_name[name].belongs_to:
            if parent in by_name and by_name[parent] not in ordered:
                visit(parent)
                ordered.append(by_name[parent])

    visit(entity.name)
    return ordered


def _payload_dict(constant: str, fields: list[EntityField]) -> list[str]:
    return [f"{constant} = {{", *[f'    "{f.name}": {_sample(f)},' for f in fields], "}"]


def _with_parents(base: str, entity: Entity, by_name: dict[str, Entity], indent: str) -> list[str]:
    parents = [p for p in entity.belongs_to if p in by_name]
    if not parents:
        return [f"{indent}{base}"]
    lines = [f"{indent}{{", f"{indent}    **{base},"]
    lines += [
        f'{indent}    "{migrations.foreign_key(p)}": _make_{snake(p)}(client)["id"],'
        for p in parents
    ]
    return [*lines, f"{indent}}}"]


def render_entity_test(entity: Entity, entities: list[Entity] | None = None) -> str:
    by_name = {e.name: e for e in (entities or [entity])}
    parents = [p for p in entity.belongs_to if p in by_name]
    if not parents:
        return _render_plain_entity_test(entity)
    var = snake(entity.name)
    plural = snake(pluralize(entity.name))
    prefix = "/" + kebab(pluralize(entity.name))
    comparable = [f for f in entity.fields if f.type not in {"date", "datetime"}]
    target = next((f for f in entity.fields if f.type in {"string", "text"}), entity.fields[0])
    item = 'f"' + prefix + "/{created['id']}\""
    ancestors = _ancestors(entity, by_name)

    lines = ["from typing import Any", "", "from fastapi.testclient import TestClient", ""]
    lines += _payload_dict("PAYLOAD", entity.fields)
    for ancestor in ancestors:
        lines += _payload_dict(snake(ancestor.name).upper(), ancestor.fields)
    for ancestor in ancestors:
        body = _with_parents(snake(ancestor.name).upper(), ancestor, by_name, "        ")
        path = f'"/{kebab(pluralize(ancestor.name))}"'
        call = (
            _call("    ", "response = client.post", f"{path}, json={body[0].strip()}")
            if len(body) == 1
            else ["    response = client.post(", f"        {path},",
                  "        json=" + body[0].strip(), *body[1:-1], body[-1] + ",", "    )"]
        )  # fmt: skip
        lines += [
            "", "",
            f"def _make_{snake(ancestor.name)}(client: TestClient) -> dict[str, Any]:",
            *call,
            "    assert response.status_code == 201, response.text",
            "    created: dict[str, Any] = response.json()",
            "    return created",
        ]  # fmt: skip
    payload = _with_parents("PAYLOAD", entity, by_name, "    ")
    lines += ["", "", "def _payload(client: TestClient) -> dict[str, Any]:",
              "    return " + payload[0].strip(), *payload[1:]]  # fmt: skip
    first_key = migrations.foreign_key(parents[0])
    first_parent = parents[0]
    lines += [
        "",
        "",
        f"def test_create_and_get_{var}(client: TestClient) -> None:",
        "    payload = _payload(client)",
        f'    response = client.post("{prefix}", json=payload)',
        "    assert response.status_code == 201, response.text",
        "    created = response.json()",
        *[_assert_equals("created", f) for f in comparable],
        *[f'    assert created["{migrations.foreign_key(p)}"] == payload["{migrations.foreign_key(p)}"]'
          for p in parents],
        f"    fetched = client.get({item})",
        "    assert fetched.status_code == 200",
        '    assert fetched.json()["id"] == created["id"]',
        "",
        "",
        f"def test_list_{var}_supports_pagination(client: TestClient) -> None:",
        "    for _ in range(3):",
        f'        assert client.post("{prefix}", json=_payload(client)).status_code == 201',
        f'    page = client.get("{prefix}", params={{"limit": 2, "offset": 1}})',
        "    assert page.status_code == 200",
        "    assert len(page.json()) == 2",
        "",
        "",
        f"def test_update_{var}(client: TestClient) -> None:",
        f'    created = client.post("{prefix}", json=_payload(client)).json()',
        *_call("    ", "response = client.patch",
               f'{item}, json={{"{target.name}": {_sample(target, updated=True)}}}'),
        "    assert response.status_code == 200",
    ]  # fmt: skip
    if target.type not in {"date", "datetime"}:
        lines.append(_assert_equals("response.json()", target, updated=True))
    lines += [
        "",
        "",
        f"def test_delete_{var}(client: TestClient) -> None:",
        f'    created = client.post("{prefix}", json=_payload(client)).json()',
        f"    assert client.delete({item}).status_code == 204",
        f"    assert client.get({item}).status_code == 404",
        "",
        "",
        f"def test_unknown_{var}_returns_404(client: TestClient) -> None:",
        f'    assert client.get("{prefix}/999999").status_code == 404',
        "",
        "",
        f"def test_create_{var}_rejects_missing_required_fields(client: TestClient) -> None:",
        f'    assert client.post("{prefix}", json={{}}).status_code == 422',
        "",
        "",
        f"def test_{var}_keeps_its_{snake(first_parent)}(client: TestClient) -> None:",
        f'    created = client.post("{prefix}", json=_payload(client)).json()',
        *_call("    ", "response = client.patch", f'{item}, json={{"{first_key}": None}}'),
        "    assert response.status_code == 422",
        "",
        "",
        f"def test_{var}_requires_an_existing_{snake(first_parent)}(client: TestClient) -> None:",
        f'    payload = {{**_payload(client), "{first_key}": 999999}}',
        f'    response = client.post("{prefix}", json=payload)',
        "    assert response.status_code == 422",
        f'    assert response.json()["detail"] == "{first_parent} 999999 not found"',
        "",
        "",
        f"def test_{plural}_can_be_filtered_by_{snake(first_parent)}(client: TestClient) -> None:",
        "    mine = _payload(client)",
        f'    assert client.post("{prefix}", json=mine).status_code == 201',
        f'    assert client.post("{prefix}", json=_payload(client)).status_code == 201',
        f'    page = client.get("{prefix}", params={{"{first_key}": mine["{first_key}"]}})',
        f'    assert [row["{first_key}"] for row in page.json()] == [mine["{first_key}"]]',
        "",
        "",
        f"def test_deleting_a_{snake(first_parent)}_deletes_its_{plural}(client: TestClient) -> None:",
        f'    created = client.post("{prefix}", json=_payload(client)).json()',
        f'    parent = f"/{kebab(pluralize(first_parent))}/{{created[\'{first_key}\']}}"',
        "    assert client.delete(parent).status_code == 204",
        f"    assert client.get({item}).status_code == 404",
    ]  # fmt: skip
    return "\n".join(lines) + "\n"


def render_pyproject(name: str, summary: str) -> str:
    safe_summary = summary.replace('"', "'").replace("\n", " ")[:200]
    return _src(f'''
        [project]
        name = "{name}"
        version = "0.1.0"
        description = "{safe_summary}"
        readme = "README.md"
        requires-python = ">=3.12"
        dependencies = [
            "fastapi>=0.115",
            "uvicorn[standard]>=0.34",
            "sqlalchemy>=2.0.36",
            "alembic>=1.16",
            "pydantic>=2.10",
            "psycopg[binary]>=3.2",
        ]

        [dependency-groups]
        dev = ["pytest>=8.3", "httpx2>=2.16", "ruff>=0.9"]

        [build-system]
        requires = ["hatchling>=1.26"]
        build-backend = "hatchling.build"

        [tool.hatch.build.targets.wheel]
        packages = ["app"]

        [tool.ruff]
        line-length = 100
        target-version = "py312"

        [tool.ruff.lint]
        select = ["E", "F", "I", "B", "UP"]

        [tool.pytest.ini_options]
        testpaths = ["tests"]
        pythonpath = ["."]
    ''')


def render_readme(name: str, summary: str, architecture: ArchitectureSpec) -> str:
    endpoint_rows = "\n".join(
        f"| `{e.method}` | `{e.path}` | {e.summary} |" for e in architecture.endpoints
    )
    return (
        _src(f"""
        # {name}

        {summary}

        > Generated by the Autonomous AI Software Engineering Platform (offline provider).
        > Review the code before deploying it anywhere that matters.

        ## Run locally

        ```bash
        python -m venv .venv && . .venv/bin/activate
        pip install -e . pytest httpx2 ruff
        uvicorn app.main:app --reload
        ```

        Open http://localhost:8000/docs for the interactive OpenAPI UI.

        ## Database migrations

        The schema is owned by Alembic (`migrations/`); the app never creates tables itself.

        ```bash
        alembic upgrade head                              # apply every migration
        alembic revision --autogenerate -m "add a column" # after changing app/models.py
        alembic downgrade -1                              # roll the last one back
        ```

        `DATABASE_URL` selects the database (SQLite `./app.db` by default). The container runs
        `alembic upgrade head` before it starts the server.

        ## Test

        ```bash
        ruff check . && pytest
        ```

        The suite builds its database with the migrations, and `tests/test_migrations.py` checks
        that they upgrade, match the models, and downgrade cleanly. Set `TEST_DATABASE_URL` to run
        that check against PostgreSQL too.

        ## Container

        ```bash
        docker build -t {name}:local .
        docker run --rm -p 8000:8000 {name}:local
        ```

        ## API

        | Method | Path | Description |
        | --- | --- | --- |
        """)
        + endpoint_rows
        + "\n\n"
        + _src("""
        ## Deployment

        * `deploy/k8s/` - Kustomize base (Deployment, Service). Set `DATABASE_URL` through the
          referenced Secret.
        * `deploy/terraform/` - AWS ECR + ECS Fargate service. Run `terraform init && terraform
          plan` with your own VPC and subnets; nothing is applied automatically.

        See `ARCHITECTURE.md` for components and design decisions.
        """)
    )


def render_project(
    project: str, summary: str, architecture: ArchitectureSpec
) -> list[GeneratedFile]:
    entities = relations.normalise(architecture.entities)
    title = " ".join(word.capitalize() for word in project.split("-"))
    files = [
        GeneratedFile(path="pyproject.toml", content=render_pyproject(project, summary),
                      description="Project metadata, dependencies and tool configuration"),
        GeneratedFile(path="README.md", content=render_readme(project, summary, architecture),
                      description="How to run, test and deploy the service"),
        GeneratedFile(path="app/__init__.py", content="", description="Application package"),
        GeneratedFile(path="app/db.py", content=render_db(),
                      description="Engine, session factory and declarative base"),
        GeneratedFile(path="app/models.py", content=render_models(entities),
                      description="SQLAlchemy ORM models"),
        GeneratedFile(path="app/schemas.py", content=render_schemas(entities),
                      description="Pydantic request and response schemas"),
        GeneratedFile(path="app/main.py", content=render_main(title, entities),
                      description="FastAPI application and router wiring"),
        GeneratedFile(path="app/routers/__init__.py", content="", description="Router package"),
        GeneratedFile(path="tests/__init__.py", content="", description="Test package"),
        GeneratedFile(path="tests/conftest.py", content=render_conftest(),
                      description="Migrated SQLite database and HTTP client fixture"),
        GeneratedFile(path="alembic.ini", content=migrations.ALEMBIC_INI,
                      description="Alembic configuration"),
        GeneratedFile(path="migrations/env.py", content=migrations.ENV_PY,
                      description="Alembic environment bound to app.models and DATABASE_URL"),
        GeneratedFile(path="migrations/script.py.mako", content=migrations.SCRIPT_MAKO,
                      description="Template for new migration scripts"),
        GeneratedFile(path="migrations/versions/0001_initial.py",
                      content=migrations.render_initial_migration(entities),
                      description="Initial schema: tables, foreign keys and indexes"),
        GeneratedFile(path="tests/test_migrations.py", content=migrations.MIGRATIONS_TEST,
                      description="Migrations upgrade, match the models and downgrade"),
        GeneratedFile(path="tests/test_health.py", content=_src("""
            from fastapi.testclient import TestClient


            def test_healthz(client: TestClient) -> None:
                response = client.get("/healthz")
                assert response.status_code == 200
                assert response.json() == {"status": "ok"}
        """), description="Health endpoint test"),
    ]  # fmt: skip
    for entity in entities:
        module = snake(pluralize(entity.name))
        files.append(
            GeneratedFile(
                path=f"app/routers/{module}.py",
                content=render_router(entity, entities),
                description=f"CRUD endpoints for {pluralize(entity.name).lower()}",
            )
        )
        files.append(
            GeneratedFile(
                path=f"tests/test_{module}.py",
                content=render_entity_test(entity, entities),
                description=f"API tests for {pluralize(entity.name).lower()}",
            )
        )
    return files
