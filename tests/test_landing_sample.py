"""The landing page shows real output, and this test keeps it that way.

``frontend/src/landing/sample.json`` is a snapshot of one offline pipeline run on the library spec:
stage summaries and log lines, the file list, a few complete files, the validation result and
the review. The test re-runs the pipeline and fails if the snapshot has drifted from what the
code produces today. To refresh it after an intentional change to the offline provider:

    AUTOSWE_UPDATE_SAMPLE=1 uv run pytest tests/test_landing_sample.py
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from autoswe.llm.offline import OfflineProvider
from autoswe.pipeline.agents import PipelineState, default_agents
from autoswe.pipeline.artifacts import STAGE_LABELS
from autoswe.schemas import language_for
from tests.conftest import LIBRARY_SPEC

SAMPLE = Path(__file__).resolve().parents[1] / "frontend" / "src" / "landing" / "sample.json"

# Complete files shown in the landing page's code viewer, in display order.
SHOWCASE = (
    "app/routers/books.py",
    "app/models.py",
    "tests/test_books.py",
    "Dockerfile",
    ".github/workflows/ci.yml",
    "deploy/k8s/deployment.yaml",
    "deploy/terraform/main.tf",
)


async def build_sample() -> dict[str, Any]:
    provider = OfflineProvider()
    state = PipelineState(spec=LIBRARY_SPEC)
    stages: list[dict[str, Any]] = []
    for agent in default_agents():
        logs: list[str] = []

        async def emit(message: str, logs: list[str] = logs) -> None:
            logs.append(message)

        result = await agent.run(state, provider, emit)
        stages.append(
            {
                "name": agent.stage.value,
                "label": STAGE_LABELS[agent.stage],
                "logs": logs,
                "summary": result.summary,
            }
        )

    requirements = state.require_requirements()
    architecture = state.require_architecture()
    assert state.validation is not None and state.review is not None
    files = [
        {
            "path": path,
            "size": len(file.content.encode()),
            "stage": state.file_origin[path].value,
            "language": language_for(path),
        }
        for path, file in sorted(state.files.items())
    ]
    return {
        "spec": LIBRARY_SPEC,
        "provider": {"name": provider.name, "model": provider.model},
        "stages": stages,
        "requirements": {
            "project_name": requirements.project_name,
            "functional_requirements": requirements.functional_requirements,
            "user_stories": requirements.user_stories,
            "entities": [
                {"name": e.name, "fields": [[f.name, f.type, f.required] for f in e.fields]}
                for e in requirements.entities
            ],
        },
        "architecture": {
            "style": architecture.style,
            "components": [
                {"name": c.name, "technology": c.technology} for c in architecture.components
            ],
            "endpoints": [[e.method, e.path] for e in architecture.endpoints],
            "decisions": [d.title for d in architecture.decisions],
            "deployment": {
                "cloud_target": architecture.deployment.cloud_target,
                "database": architecture.deployment.database,
                "replicas": architecture.deployment.replicas,
                "container_port": architecture.deployment.container_port,
            },
        },
        "files": files,
        "showcase": [
            {"path": path, "language": language_for(path), "content": state.files[path].content}
            for path in SHOWCASE
        ],
        "validation": {
            "files_checked": state.validation.files_checked,
            "errors": len(state.validation.errors),
            "warnings": sum(i.severity == "warning" for i in state.validation.issues),
            "passed": state.validation.passed,
        },
        "review": {
            "score": state.review.score,
            "summary": state.review.summary,
            "findings": [f.model_dump() for f in state.review.findings],
        },
    }


def render(sample: dict[str, Any]) -> str:
    return json.dumps(sample, indent=2, ensure_ascii=False) + "\n"


async def test_landing_sample_matches_the_offline_pipeline() -> None:
    expected = render(await build_sample())
    if os.environ.get("AUTOSWE_UPDATE_SAMPLE") == "1":
        SAMPLE.parent.mkdir(parents=True, exist_ok=True)
        SAMPLE.write_text(expected, encoding="utf-8", newline="\n")
    assert SAMPLE.is_file(), f"missing {SAMPLE}; run with AUTOSWE_UPDATE_SAMPLE=1"
    assert SAMPLE.read_text(encoding="utf-8") == expected, (
        "The landing page sample no longer matches the offline pipeline. "
        "Refresh it with AUTOSWE_UPDATE_SAMPLE=1 uv run pytest tests/test_landing_sample.py"
    )
