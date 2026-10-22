"""Command-line entry point.

* ``autoswe serve``    - API, UI and in-process worker (default)
* ``autoswe worker``   - the queue worker only, with health, readiness and metrics endpoints
* ``autoswe migrate``  - apply database migrations and exit
* ``autoswe generate`` - run the pipeline once, locally, and write the project to a directory
* ``autoswe sandbox-runner`` - the sandbox runner service (the only process with Docker access)
* ``autoswe usage``    - token, budget and cost ledger per principal (reads the database)
* ``autoswe eval``     - run or record the eval suite
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path
from typing import Any

import uvicorn

from autoswe.config import Settings, get_settings
from autoswe.evals.cli import add_arguments as add_eval_arguments
from autoswe.evals.cli import main as eval_main
from autoswe.logging_config import configure_logging
from autoswe.migrations.runner import upgrade_to_head


async def _generate(spec: str, out: Path, provider_name: str | None, sandbox: str | None) -> int:
    # Imported lazily so `serve` does not pay for it.
    from autoswe.llm.registry import build_registry  # noqa: PLC0415
    from autoswe.pipeline.agents import (  # noqa: PLC0415
        Agent,
        PipelineState,
        StageResult,
        default_agents,
        run_pipeline,
    )
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

    agents = default_agents()
    print(f"[{agents[0].stage.value}]", file=sys.stderr)  # noqa: T201

    async def emit(message: str) -> None:
        print(f"    {message}", file=sys.stderr)  # noqa: T201

    async def on_stage(agent: Agent, result: StageResult) -> None:
        print(f"    -> {result.summary}", file=sys.stderr)  # noqa: T201
        position = agents.index(agent)
        if position + 1 < len(agents):
            print(f"[{agents[position + 1].stage.value}]", file=sys.stderr)  # noqa: T201

    await run_pipeline(state, llm, emit, agents=agents, on_stage=on_stage)

    for file in state.files.values():
        target = out / file.path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(file.content, encoding="utf-8", newline="\n")
    report = state.validation
    print(f"wrote {len(state.files)} files to {out}", file=sys.stderr)  # noqa: T201
    tests = state.test_result
    tests_ok = tests is None or tests.status.value == "passed"
    return 0 if report is not None and report.passed and tests_ok else 1


async def _usage(settings: Settings, principal: str | None, all_periods: bool) -> int:
    from autoswe.budget import BudgetPolicy, UsageRepository, period_bounds  # noqa: PLC0415
    from autoswe.db import Database  # noqa: PLC0415
    from autoswe.pricing import usd  # noqa: PLC0415

    policy = BudgetPolicy.from_settings(settings)
    current, _ = period_bounds(policy.period)
    db = Database(settings.database_url)
    try:
        async with db.session() as session:
            rows = await UsageRepository(session).list(principal)
    finally:
        await db.dispose()
    rows = [row for row in rows if all_periods or row.period_start == current]
    header = f"{'principal':<20} {'period':<10} {'used':>12} {'reserved':>10} {'limit':>12} "
    print(header + f"{'calls':>7} {'cost USD':>12}")  # noqa: T201
    for row in rows:
        limit = policy.limit_for(row.principal)
        cost = f"{usd(row.cost_microusd):.6f}" + ("*" if row.unpriced_calls else "")
        print(  # noqa: T201
            f"{row.principal:<20} {row.period_start.date().isoformat():<10} "
            f"{row.used_tokens:>12} {row.reserved_tokens:>10} "
            f"{'unlimited' if limit is None else limit:>12} {row.model_calls:>7} {cost:>12}"
        )
    if any(row.unpriced_calls for row in rows):
        print("* includes calls to models missing from the pricing table")  # noqa: T201
    return 0


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="autoswe")
    sub = parser.add_subparsers(dest="command")
    serve = sub.add_parser("serve", help="Run the API, UI and in-process worker (default)")
    serve.add_argument("--reload", action="store_true", help="Auto-reload on code changes (dev)")
    sub.add_parser("migrate", help="Apply database migrations and exit")
    sub.add_parser("worker", help="Run only the queue worker (health, readiness, metrics)")
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
    usage = sub.add_parser("usage", help="Token, budget and cost ledger per principal")
    usage.add_argument("--principal", default=None, help="Only this principal")
    usage.add_argument("--all-periods", action="store_true", help="Not just the current period")
    evaluate = sub.add_parser("eval", help="Run or record the eval suite (fixtures in evals/)")
    add_eval_arguments(evaluate)
    args = parser.parse_args(argv)

    if args.command == "eval":
        configure_logging("WARNING", "console")
        sys.exit(asyncio.run(eval_main(args)))

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
    if args.command == "usage":
        configure_logging("WARNING", "console")
        if settings.db_auto_migrate:
            upgrade_to_head(settings.database_url)
        sys.exit(asyncio.run(_usage(settings, args.principal, args.all_periods)))
    if args.command == "migrate":
        configure_logging(settings.log_level, settings.log_format)
        upgrade_to_head(settings.database_url)
        return
    if args.command == "worker":
        from autoswe.app import create_app  # noqa: PLC0415

        worker_settings = settings.model_copy(update={"serve_api": False, "worker_enabled": True})
        uvicorn.run(
            create_app(worker_settings),
            host=settings.host,
            port=settings.port,
            proxy_headers=False,
            log_config=None,
            timeout_graceful_shutdown=5,  # no long-lived streams on a worker
        )
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
