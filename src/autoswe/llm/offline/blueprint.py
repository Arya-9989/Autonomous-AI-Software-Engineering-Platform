"""Deterministic architecture design used by the offline provider."""

from __future__ import annotations

from autoswe.pipeline.artifacts import (
    ArchitectureSpec,
    Component,
    DecisionRecord,
    DeploymentPlan,
    Endpoint,
    Requirements,
    TechChoice,
)
from autoswe.pipeline.naming import kebab, pluralize, snake
from autoswe.stacks import Stack


def resource_path(entity_name: str) -> str:
    return "/" + kebab(pluralize(entity_name))


def _article(noun: str) -> str:
    return "an" if noun[:1].lower() in "aeiou" else "a"


def design(requirements: Requirements, stack: Stack = Stack.PYTHON) -> ArchitectureSpec:
    endpoints = _endpoints(requirements)
    if stack is not Stack.PYTHON:
        return _design_in_memory(requirements, endpoints, stack)
    return _design_python(requirements, endpoints)


def _endpoints(requirements: Requirements) -> list[Endpoint]:
    endpoints: list[Endpoint] = [
        Endpoint(method="GET", path="/healthz", summary="Liveness probe", entity=None)
    ]
    for entity in requirements.entities:
        base = resource_path(entity.name)
        item = f"{base}/{{{snake(entity.name)}_id}}"
        plural = pluralize(entity.name).lower()
        one = f"{_article(entity.name)} {entity.name.lower()}"
        endpoints += [
            Endpoint(method="GET", path=base, summary=f"List {plural}", entity=entity.name),
            Endpoint(method="POST", path=base, summary=f"Create {one}",
                     entity=entity.name),
            Endpoint(method="GET", path=item, summary=f"Get one {entity.name.lower()}",
                     entity=entity.name),
            Endpoint(method="PATCH", path=item, summary=f"Update {one}",
                     entity=entity.name),
            Endpoint(method="DELETE", path=item, summary=f"Delete {one}",
                     entity=entity.name),
        ]  # fmt: skip
    return endpoints


def _design_python(requirements: Requirements, endpoints: list[Endpoint]) -> ArchitectureSpec:
    return ArchitectureSpec(
        style="Modular monolith (single stateless HTTP service)",
        overview=(
            f"{requirements.summary} The service is a stateless FastAPI application; "
            "each entity gets its own router, Pydantic schemas and SQLAlchemy model. State lives "
            "in a relational database so replicas can scale horizontally."
        ),
        components=[
            Component(
                name="HTTP API",
                responsibility="Routing, request validation and JSON serialisation",
                technology="FastAPI + Pydantic v2",
                depends_on=["Persistence"],
            ),
            Component(
                name="Persistence",
                responsibility="ORM models, sessions and schema creation",
                technology="SQLAlchemy 2.0",
                depends_on=["Database"],
            ),
            Component(
                name="Database",
                responsibility="Durable storage for all entities",
                technology="PostgreSQL (SQLite locally)",
                depends_on=[],
            ),
            Component(
                name="Delivery pipeline",
                responsibility="Lint, test, build and publish the container image",
                technology="GitHub Actions + Docker",
                depends_on=["HTTP API"],
            ),
        ],
        entities=requirements.entities,
        endpoints=endpoints,
        tech_stack=[
            TechChoice(
                layer="Language",
                choice="Python 3.12",
                rationale="Mature typing and a large ecosystem for web services",
            ),
            TechChoice(
                layer="Web framework",
                choice="FastAPI",
                rationale="Typed request validation and OpenAPI docs for free",
            ),
            TechChoice(
                layer="ORM",
                choice="SQLAlchemy 2.0",
                rationale="Database-agnostic; same code on SQLite and PostgreSQL",
            ),
            TechChoice(
                layer="Packaging",
                choice="Docker (python:3.12-slim, non-root)",
                rationale="Reproducible runtime with a small attack surface",
            ),
            TechChoice(
                layer="Orchestration",
                choice="Kubernetes Deployment + Service",
                rationale="Rolling updates, probes and horizontal scaling",
            ),
            TechChoice(
                layer="Infrastructure as code",
                choice="Terraform (AWS ECS Fargate)",
                rationale="Declarative, reviewable infrastructure changes",
            ),
        ],
        deployment=DeploymentPlan(
            container_port=8000,
            replicas=2,
            database="postgres",
            cloud_target="aws-ecs-fargate",
            environment_variables=["DATABASE_URL"],
        ),
        decisions=[
            DecisionRecord(
                title="One service, one database",
                decision="Ship a single deployable instead of microservices.",
                rationale="The domain is small; a monolith keeps transactions and deploys simple.",
                alternatives=["Microservice per entity", "Serverless functions"],
            ),
            DecisionRecord(
                title="Create schema on startup",
                decision="Call metadata.create_all() at startup instead of migrations.",
                rationale="Keeps the scaffold minimal; adopt Alembic before schemas evolve.",
                alternatives=["Alembic migrations from day one"],
            ),
        ],
    )


_IN_MEMORY: dict[Stack, dict[str, str]] = {
    Stack.TYPESCRIPT: {
        "language": "TypeScript 5 on Node.js 24",
        "why_language": "Static types with no build step: Node runs the TypeScript directly",
        "http": "node:http with a small typed router",
        "tests": "node:test with fetch against an ephemeral server",
        "image": "Docker (node:24-slim, non-root)",
    },
    Stack.GO: {
        "language": "Go 1.27",
        "why_language": "One static binary, fast start-up and a strong standard library",
        "http": "net/http ServeMux with method and path patterns",
        "tests": "testing with net/http/httptest",
        "image": "Docker (multi-stage, distroless static, non-root)",
    },
}


def _design_in_memory(
    requirements: Requirements, endpoints: list[Endpoint], stack: Stack
) -> ArchitectureSpec:
    """The same API on a dependency-free stack: no ORM, no database, state in memory."""
    tech = _IN_MEMORY[stack]
    return ArchitectureSpec(
        style="Single-process HTTP service with in-memory storage",
        overview=(
            f"{requirements.summary} The service is written in {tech['language']} using only "
            "its standard library. Each entity has a typed model, input validation and CRUD "
            "handlers; data is held in memory, so it runs as one replica until a database "
            "replaces the store."
        ),
        components=[
            Component(
                name="HTTP API",
                responsibility="Routing, request validation and JSON serialisation",
                technology=tech["http"],
                depends_on=["Store"],
            ),
            Component(
                name="Store",
                responsibility="Thread-safe in-memory storage per entity",
                technology="Standard library collections",
                depends_on=[],
            ),
            Component(
                name="Delivery pipeline",
                responsibility="Lint, type check, test, build and publish the image",
                technology="GitHub Actions + Docker",
                depends_on=["HTTP API"],
            ),
        ],
        entities=requirements.entities,
        endpoints=endpoints,
        tech_stack=[
            TechChoice(layer="Language", choice=tech["language"], rationale=tech["why_language"]),
            TechChoice(
                layer="HTTP",
                choice=tech["http"],
                rationale="No third-party runtime dependencies to audit or update",
            ),
            TechChoice(
                layer="Tests",
                choice=tech["tests"],
                rationale="Exercises the real HTTP surface, in process",
            ),
            TechChoice(
                layer="Packaging",
                choice=tech["image"],
                rationale="Reproducible runtime with a small attack surface",
            ),
            TechChoice(
                layer="Orchestration",
                choice="Kubernetes Deployment + Service",
                rationale="Rolling updates and probes",
            ),
            TechChoice(
                layer="Infrastructure as code",
                choice="Terraform (AWS ECS Fargate)",
                rationale="Declarative, reviewable infrastructure changes",
            ),
        ],
        deployment=DeploymentPlan(
            container_port=8000,
            replicas=1,
            database="memory",
            cloud_target="aws-ecs-fargate",
            environment_variables=["PORT"],
        ),
        decisions=[
            DecisionRecord(
                title="Standard library only",
                decision="Use no third-party runtime dependencies.",
                rationale="Nothing to install or patch at run time; the scaffold stays small.",
                alternatives=["A web framework (Express, Fastify, chi, gin)"],
            ),
            DecisionRecord(
                title="In-memory storage",
                decision="Keep data in memory behind a small store interface.",
                rationale="No database to provision for the first version; the store is the "
                "seam where a database goes next, and until then it runs as one replica.",
                alternatives=["PostgreSQL from day one", "SQLite"],
            ),
        ],
    )
