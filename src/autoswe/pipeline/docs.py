"""Platform-rendered documentation (deterministic; not produced by an LLM)."""

from __future__ import annotations

import re

from autoswe.pipeline.artifacts import ArchitectureSpec, Requirements

_UNSAFE_LABEL = re.compile(r"[\"\[\]{}()<>|`]")


def _label(text: str) -> str:
    return _UNSAFE_LABEL.sub("", text).strip() or "component"


def mermaid_diagram(architecture: ArchitectureSpec) -> str:
    ids = {component.name: f"c{index}" for index, component in enumerate(architecture.components)}
    lines = ["flowchart LR"]
    for component in architecture.components:
        lines.append(
            f'    {ids[component.name]}["{_label(component.name)}<br/>'
            f'<small>{_label(component.technology)}</small>"]'
        )
    for component in architecture.components:
        for dependency in component.depends_on:
            if dependency in ids:
                lines.append(f"    {ids[component.name]} --> {ids[dependency]}")
    return "\n".join(lines)


def architecture_markdown(requirements: Requirements, architecture: ArchitectureSpec) -> str:
    out = [
        f"# {requirements.project_name} - architecture",
        "",
        architecture.overview,
        "",
        f"**Style:** {architecture.style}",
        "",
        "## Components",
        "",
        "```mermaid",
        mermaid_diagram(architecture),
        "```",
        "",
        "| Component | Responsibility | Technology |",
        "| --- | --- | --- |",
    ]
    out += [f"| {c.name} | {c.responsibility} | {c.technology} |" for c in architecture.components]
    out += ["", "## Data model", ""]
    for entity in architecture.entities:
        out.append(f"### {entity.name}")
        out.append("")
        out.append(entity.description)
        out.append("")
        out.append("| Field | Type | Required |")
        out.append("| --- | --- | --- |")
        out += [
            f"| `{f.name}` | {f.type} | {'yes' if f.required else 'no'} |" for f in entity.fields
        ]
        out.append("")
    out += ["## API", "", "| Method | Path | Summary |", "| --- | --- | --- |"]
    out += [f"| `{e.method}` | `{e.path}` | {e.summary} |" for e in architecture.endpoints]
    out += ["", "## Technology choices", "", "| Layer | Choice | Why |", "| --- | --- | --- |"]
    out += [f"| {t.layer} | {t.choice} | {t.rationale} |" for t in architecture.tech_stack]
    out += ["", "## Decisions", ""]
    for index, decision in enumerate(architecture.decisions, start=1):
        out += [
            f"### ADR-{index:03d}: {decision.title}",
            "",
            f"**Decision:** {decision.decision}",
            "",
            f"**Rationale:** {decision.rationale}",
            "",
            f"**Alternatives considered:** {', '.join(decision.alternatives) or 'none'}",
            "",
        ]
    plan = architecture.deployment
    out += [
        "## Deployment",
        "",
        f"* Container port: {plan.container_port}",
        f"* Replicas: {plan.replicas}",
        f"* Database: {plan.database}",
        f"* Terraform target: {plan.cloud_target} (generated, never applied by the platform)",
        f"* Environment: {', '.join(plan.environment_variables) or 'none'}",
        "",
        "## Requirements traceability",
        "",
    ]
    out += [f"* {item}" for item in requirements.functional_requirements]
    return "\n".join(out) + "\n"
