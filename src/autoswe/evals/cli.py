"""``autoswe eval run`` and ``autoswe eval record``.

Exit codes: 0 the gate passed (or was skipped with ``--no-check``), 1 a regression or a
fixture error, 2 a usage or configuration problem (missing key, missing recording).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

DEFAULT_EVALS_DIR = Path("evals")


def add_arguments(parser: argparse.ArgumentParser) -> None:
    actions = parser.add_subparsers(dest="eval_command", required=True)
    for name, help_text in (
        ("run", "Score the pipeline on the fixture suite and check the thresholds"),
        ("record", "Refresh the recorded model responses the suite replays"),
    ):
        action = actions.add_parser(name, help=help_text)
        action.add_argument("--evals-dir", type=Path, default=DEFAULT_EVALS_DIR)
        action.add_argument("--only", default=None, help="Comma-separated fixture names")
        action.add_argument(
            "--sandbox",
            choices=["none", "docker", "local", "remote"],
            default=None,
            help="Where generated tests run (default: SANDBOX_BACKEND)",
        )
    run = actions.choices["run"]
    run.add_argument(
        "--live",
        action="store_true",
        help="Call the Anthropic API instead of replaying (needs ANTHROPIC_API_KEY)",
    )
    run.add_argument("--out", type=Path, default=Path("eval-report"))
    run.add_argument("--no-check", action="store_true", help="Report only; do not gate")
    record = actions.choices["record"]
    record.add_argument("--source", choices=["offline-provider", "anthropic"],
                        default="offline-provider")  # fmt: skip


def _err(message: str) -> None:
    print(f"autoswe eval: {message}", file=sys.stderr)  # noqa: T201


async def main(args: argparse.Namespace) -> int:  # noqa: PLR0911 - one exit per failure mode
    from autoswe.config import Settings  # noqa: PLC0415
    from autoswe.evals.fixtures import load_fixtures  # noqa: PLC0415
    from autoswe.evals.harness import (  # noqa: PLC0415
        load_recordings,
        record_suite,
        run_suite,
        write_report,
    )
    from autoswe.evals.recording import PROVENANCE, ReplayProvider  # noqa: PLC0415
    from autoswe.evals.scoring import Thresholds  # noqa: PLC0415
    from autoswe.llm.base import LLMProvider  # noqa: PLC0415
    from autoswe.llm.offline import OfflineProvider  # noqa: PLC0415
    from autoswe.sandbox.factory import build_sandbox  # noqa: PLC0415

    overrides: dict[str, object] = {"auth_disabled": True}
    if args.sandbox is not None:
        overrides["sandbox_backend"] = args.sandbox
    try:
        settings = Settings(**overrides)  # type: ignore[arg-type]  # dynamic overrides
        only = [name.strip() for name in args.only.split(",")] if args.only else None
        fixtures = load_fixtures(args.evals_dir / "fixtures", only)
    except ValueError as exc:
        _err(str(exc))
        return 2
    sandbox = build_sandbox(settings)
    recordings = args.evals_dir / "recordings"
    rounds = settings.sandbox_max_repair_rounds

    def anthropic() -> LLMProvider | None:
        if not settings.anthropic_configured:
            _err("this mode calls the Anthropic API: set ANTHROPIC_API_KEY")
            return None
        from autoswe.llm.anthropic_provider import AnthropicProvider  # noqa: PLC0415

        return AnthropicProvider.from_settings(settings)

    if args.eval_command == "record":
        inner = OfflineProvider() if args.source == "offline-provider" else anthropic()
        if inner is None:
            return 2
        written = await record_suite(
            fixtures, inner, source=args.source, sandbox=sandbox, repair_rounds=rounds,
            recordings=recordings,
        )  # fmt: skip
        for path in written:
            print(f"recorded {path}", file=sys.stderr)  # noqa: T201
        return 0

    try:
        thresholds = Thresholds.load(args.evals_dir / "thresholds.yaml")
    except (OSError, ValueError) as exc:
        _err(f"cannot read thresholds: {exc}")
        return 2
    if args.live:
        live = anthropic()
        if live is None:
            return 2
        run = await run_suite(
            fixtures, lambda _: live, sandbox=sandbox, repair_rounds=rounds, mode="live",
            source="anthropic", provenance=PROVENANCE["anthropic"],
        )  # fmt: skip
    else:
        try:
            loaded = load_recordings(recordings, fixtures)
        except (OSError, ValueError) as exc:
            _err(str(exc))
            return 2
        sources = {recording.source for recording in loaded.values()}
        source = sources.pop() if len(sources) == 1 else "mixed"
        run = await run_suite(
            fixtures, lambda fixture: ReplayProvider(loaded[fixture.name]), sandbox=sandbox,
            repair_rounds=rounds, mode="replay", source=source,
            provenance=PROVENANCE.get(source, "Recordings from more than one source."),
        )  # fmt: skip
    failures = write_report(run, thresholds, args.out)
    print((args.out / "eval-report.md").read_text(encoding="utf-8"))  # noqa: T201
    if failures and not args.no_check:
        _err(f"{len(failures)} regression(s) against {args.evals_dir / 'thresholds.yaml'}")
        return 1
    return 0
