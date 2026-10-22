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


def resource_path(entity_name: str) -> str:
    return "/" + kebab(pluralize(entity_name))


def design(requirements: Requirements) -> ArchitectureSpec:
    endpoints: list[Endpoint] = [
        Endpoint(method="GET", path="/healthz", summary="Liveness probe", entity=None)
    ]
    for entity in requirements.entities:
        base = resource_path(entity.name)
        item = f"{base}/{{{snake(entity.name)}_id}}"
        plural = pluralize(entity.name).lower()
        endpoints += [
            Endpoint(method="GET", path=base, summary=f"List {plural}", entity=entity.name),
            Endpoint(method="POST", path=base, summary=f"Create a {entity.name.lower()}",
                     entity=entity.name),
            Endpoint(method="GET", path=item, summary=f"Get one {entity.name.lower()}",
                     entity=entity.name),
            Endpoint(method="PATCH", path=item, summary=f"Update a {entity.name.lower()}",
                     entity=entity.name),
            Endpoint(method="DELETE", path=item, summary=f"Delete a {entity.name.lower()}",
                     entity=entity.name),
        ]  # fmt: skip

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
