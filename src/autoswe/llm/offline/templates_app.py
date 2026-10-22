"""Source templates for the FastAPI service the offline provider generates.

The output is a complete, runnable project: CRUD routers per entity, Pydantic schemas,
SQLAlchemy models and a pytest suite that exercises every endpoint. The platform's own test
suite runs the generated tests to prove the output actually works.
"""

from __future__ import annotations

from textwrap import dedent

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

        from sqlalchemy import create_engine
        from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

        DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./app.db")

        _connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
        engine = create_engine(DATABASE_URL, connect_args=_connect_args, pool_pre_ping=True)
        SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


        class Base(DeclarativeBase):
            pass


        def get_session() -> Iterator[Session]:
            with SessionLocal() as session:
                yield session
    ''')


def render_models(entities: list[Entity]) -> str:
    needs_date = any(f.type == "date" for e in entities for f in e.fields)
    sa_names = sorted(
        {"DateTime", "Integer"}
        | {_SA_TYPES[f.type][0].split("(")[0] for e in entities for f in e.fields}
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
        lines.append(
            "    created_at: Mapped[datetime] = mapped_column("
            "DateTime(timezone=True), default=_utcnow)"
        )
    return "\n".join(lines) + "\n"


def render_schemas(entities: list[Entity]) -> str:
    needs_date = any(f.type == "date" for e in entities for f in e.fields)
    lines = [
        '"""Pydantic request/response schemas."""',
        "",
        f"from datetime import {'date, datetime' if needs_date else 'datetime'}",
        "",
        "from pydantic import BaseModel, ConfigDict, Field",
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
        lines += ["", "", f"class {name}Update(BaseModel):"]
        for field in entity.fields:
            constraint = ", max_length=255" if field.type == "string" else ""
            lines.append(f"    {field.name}: {_py_type(field)} | None = Field(None{constraint})")
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


def render_router(entity: Entity) -> str:
    name = entity.name
    var = snake(name)
    plural = snake(pluralize(name))
    prefix = "/" + kebab(pluralize(name))
    return _src(f'''
        """CRUD endpoints for {plural}."""

        from typing import Annotated

        from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
        from sqlalchemy import select
        from sqlalchemy.orm import Session

        from app.db import get_session
        from app.models import {name}
        from app.schemas import {name}Create, {name}Read, {name}Update

        router = APIRouter(prefix="{prefix}", tags=["{plural}"])
        SessionDep = Annotated[Session, Depends(get_session)]


        def _get_or_404(session: Session, {var}_id: int) -> {name}:
            {var} = session.get({name}, {var}_id)
            if {var} is None:
                raise HTTPException(status.HTTP_404_NOT_FOUND, detail="{name} not found")
            return {var}


        @router.get("", response_model=list[{name}Read])
        def list_{plural}(
            session: SessionDep,
            limit: Annotated[int, Query(ge=1, le=200)] = 50,
            offset: Annotated[int, Query(ge=0)] = 0,
        ) -> list[{name}]:
            query = select({name}).order_by({name}.id).offset(offset).limit(limit)
            return list(session.scalars(query))


        @router.post("", response_model={name}Read, status_code=status.HTTP_201_CREATED)
        def create_{var}(payload: {name}Create, session: SessionDep) -> {name}:
            {var} = {name}(**payload.model_dump())
            session.add({var})
            session.commit()
            session.refresh({var})
            return {var}


        @router.get("/{{{var}_id}}", response_model={name}Read)
        def get_{var}({var}_id: int, session: SessionDep) -> {name}:
            return _get_or_404(session, {var}_id)


        @router.patch("/{{{var}_id}}", response_model={name}Read)
        def update_{var}(
            {var}_id: int,
            payload: {name}Update,
            session: SessionDep,
        ) -> {name}:
            {var} = _get_or_404(session, {var}_id)
            for key, value in payload.model_dump(exclude_unset=True).items():
                setattr({var}, key, value)
            session.commit()
            session.refresh({var})
            return {var}


        @router.delete("/{{{var}_id}}", status_code=status.HTTP_204_NO_CONTENT)
        def delete_{var}({var}_id: int, session: SessionDep) -> Response:
            session.delete(_get_or_404(session, {var}_id))
            session.commit()
            return Response(status_code=status.HTTP_204_NO_CONTENT)
    ''')


def render_main(title: str, entities: list[Entity]) -> str:
    imports = ", ".join(sorted(snake(pluralize(e.name)) for e in entities))
    includes = "\n".join(f"app.include_router({snake(pluralize(e.name))}.router)" for e in entities)
    return (
        _src(f'''
        """Application entry point."""

        from collections.abc import AsyncIterator
        from contextlib import asynccontextmanager

        from fastapi import FastAPI

        from app.db import Base, engine
        from app.routers import {imports}


        @asynccontextmanager
        async def lifespan(_: FastAPI) -> AsyncIterator[None]:
            Base.metadata.create_all(engine)
            yield


        app = FastAPI(title="{title}", version="0.1.0", lifespan=lifespan)
        ''')
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

        from fastapi.testclient import TestClient  # noqa: E402

        from app.db import Base, engine  # noqa: E402
        from app.main import app  # noqa: E402


        @pytest.fixture
        def client() -> Iterator[TestClient]:
            Base.metadata.drop_all(engine)
            with TestClient(app) as test_client:
                yield test_client
    """)


def _assert_equals(expr: str, field: EntityField, *, updated: bool = False) -> str:
    value = _sample(field, updated=updated)
    operator = "is" if field.type == "boolean" else "=="
    return f'    assert {expr}["{field.name}"] {operator} {value}'


def render_entity_test(entity: Entity) -> str:
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
        "    response = client.patch(",
        f'        {item}, json={{"{target.name}": {_sample(target, updated=True)}}}',
        "    )",
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
    if any(f.required for f in entity.fields):
        lines += [
            "",
            "",
            f"def test_create_{var}_rejects_missing_required_fields(client: TestClient) -> None:",
            f'    assert client.post("{prefix}", json={{}}).status_code == 422',
        ]
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

        ## Test

        ```bash
        ruff check . && pytest
        ```

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
    entities = architecture.entities
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
                      description="Isolated SQLite database and HTTP client fixture"),
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
                content=render_router(entity),
                description=f"CRUD endpoints for {pluralize(entity.name).lower()}",
            )
        )
        files.append(
            GeneratedFile(
                path=f"tests/test_{module}.py",
                content=render_entity_test(entity),
                description=f"API tests for {pluralize(entity.name).lower()}",
            )
        )
    return files
