"""Command-line entry point.

* ``autoswe serve``    - API, UI and in-process worker (default)
* ``autoswe migrate``  - apply database migrations and exit
* ``autoswe generate`` - run the pipeline once, locally, and write the project to a directory
* ``autoswe sandbox-runner`` - the sandbox runner service (the only process with Docker access)
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path
from typing import Any

import uvicorn

from autoswe.config import Settings, get_settings
from autoswe.logging_config import configure_logging
from autoswe.migrations.runner import upgrade_to_head


async def _generate(spec: str, out: Path, provider_name: str | None, sandbox: str | None) -> int:
    # Imported lazily so `serve` does not pay for it.
    from autoswe.llm.registry import build_registry  # noqa: PLC0415
    from autoswe.pipeline.agents import PipelineState, default_agents  # noqa: PLC0415
    from autoswe.sandbox.factory import build_sandbox  # noqa: PLC0415

    # Local, one-shot run: API authentication does not apply.
    overrides: dict[str, Any] = {"auth_disabled": True}
    if sandbox is not None:
        overrides["sandbox_backend"] = sandbox
    settings = Settings(**overrides)
    llm = build_registry(settings).get(provider_name)
    state = PipelineState(
        spec=spec,
        sandbox=build_sandbox(settings),
        max_test_repair_rounds=settings.sandbox_max_repair_rounds,
    )

    async def emit(message: str) -> None:
        print(f"    {message}", file=sys.stderr)  # noqa: T201

    for agent in default_agents():
        print(f"[{agent.stage.value}]", file=sys.stderr)  # noqa: T201
        result = await agent.run(state, llm, emit)
        print(f"    -> {result.summary}", file=sys.stderr)  # noqa: T201

    for file in state.files.values():
        target = out / file.path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(file.content, encoding="utf-8", newline="\n")
    report = state.validation
    print(f"wrote {len(state.files)} files to {out}", file=sys.stderr)  # noqa: T201
    tests = state.test_result
    tests_ok = tests is None or tests.status.value == "passed"
    return 0 if report is not None and report.passed and tests_ok else 1


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="autoswe")
    sub = parser.add_subparsers(dest="command")
    serve = sub.add_parser("serve", help="Run the API, UI and in-process worker (default)")
    serve.add_argument("--reload", action="store_true", help="Auto-reload on code changes (dev)")
    sub.add_parser("migrate", help="Apply database migrations and exit")
    generate = sub.add_parser("generate", help="Run the pipeline once and write the project")
    generate.add_argument("spec", help="Specification text, or @path to read it from a file")
    generate.add_argument("--out", type=Path, required=True, help="Output directory")
    generate.add_argument("--provider", default=None, help="Provider (default: configured default)")
    generate.add_argument(
        "--sandbox",
        choices=["none", "docker", "local", "remote"],
        default=None,
        help="Run the generated tests (default: SANDBOX_BACKEND, which defaults to none)",
    )
    sub.add_parser("sandbox-runner", help="Run the sandbox runner service (needs Docker access)")
    args = parser.parse_args(argv)

    if args.command == "sandbox-runner":
        from autoswe.sandbox.service import serve as serve_runner  # noqa: PLC0415

        serve_runner()
        return

    if args.command == "generate":
        configure_logging("WARNING", "console")
        spec = args.spec
        if spec.startswith("@"):
            spec = Path(spec[1:]).read_text(encoding="utf-8")
        sys.exit(asyncio.run(_generate(spec, args.out, args.provider, args.sandbox)))

    settings = get_settings()
    if args.command == "migrate":
        configure_logging(settings.log_level, settings.log_format)
        upgrade_to_head(settings.database_url)
        return
    uvicorn.run(
        "autoswe.app:create_app",
        factory=True,
        host=settings.host,
        port=settings.port,
        reload=getattr(args, "reload", False),
        proxy_headers=True,
        forwarded_allow_ips=settings.forwarded_allow_ips,
        log_config=None,
        timeout_graceful_shutdown=30,
    )


if __name__ == "__main__":
    main()
