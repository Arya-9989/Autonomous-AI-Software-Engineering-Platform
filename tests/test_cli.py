import asyncio
from pathlib import Path
from typing import Any

import httpx
import pytest

from autoswe.__main__ import main


def test_generate_writes_a_validated_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("API_KEYS", raising=False)
    spec_file = tmp_path / "spec.txt"
    spec_file.write_text("Inventory service. Entities: Product(name, price:float, stock:int)")
    out = tmp_path / "out"

    with pytest.raises(SystemExit) as exit_info:
        main(["generate", f"@{spec_file}", "--out", str(out), "--provider", "offline"])

    assert exit_info.value.code == 0
    assert (out / "app" / "routers" / "products.py").is_file()
    assert (out / "deploy" / "terraform" / "main.tf").is_file()
    assert "products" in (out / "ARCHITECTURE.md").read_text()


def test_generate_runs_the_tests_in_the_requested_sandbox(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("API_KEYS", raising=False)
    monkeypatch.setenv("SANDBOX_TYPECHECK", "false")
    out = tmp_path / "out"
    with pytest.raises(SystemExit) as exit_info:
        main(["generate", "Track expenses with description, amount:float and spent_on:date.",
              "--out", str(out), "--provider", "offline", "--sandbox", "local"])  # fmt: skip
    assert exit_info.value.code == 0
    log = capsys.readouterr().err
    assert "Running the test suite in the local sandbox" in log
    assert "Tests passed in the sandbox" in log


async def test_usage_command_prints_the_ledger(
    harness_factory: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:  # fmt: skip
    from autoswe.config import get_settings
    from tests.conftest import make_settings
    from tests.fakes import PricedProvider, registry_with

    harness = await harness_factory(registry=registry_with(PricedProvider()),
                                    token_budgets="alice:1000000")  # fmt: skip
    job = await harness.submit()
    await harness.wait_for(job["id"], {"succeeded"})

    settings = make_settings(tmp_path)
    monkeypatch.setenv("DATABASE_URL", settings.database_url)
    monkeypatch.setenv("AUTH_DISABLED", "true")
    monkeypatch.setenv("TOKEN_BUDGETS", "alice:1000000")
    get_settings.cache_clear()
    try:
        with pytest.raises(SystemExit) as exit_info:
            await asyncio.to_thread(main, ["usage"])
    finally:
        get_settings.cache_clear()
    assert exit_info.value.code == 0
    out = capsys.readouterr().out
    line = next(row for row in out.splitlines() if row.startswith("alice"))
    assert "6000" in line and "1000000" in line and "0.029100" in line


def test_worker_command_runs_a_worker_only_app(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from autoswe.config import get_settings

    started: dict[str, Any] = {}

    def fake_run(app: Any, **kwargs: Any) -> None:
        started["app"], started["kwargs"] = app, kwargs

    monkeypatch.setattr("autoswe.__main__.uvicorn.run", fake_run)
    monkeypatch.setenv("AUTH_DISABLED", "true")
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{(tmp_path / 'w.db').as_posix()}")
    get_settings.cache_clear()
    try:
        main(["worker"])
    finally:
        get_settings.cache_clear()
    services = started["app"].state.services
    assert services.settings.serve_api is False and services.settings.worker_enabled is True

    async def probe(path: str) -> int:
        transport = httpx.ASGITransport(app=started["app"])
        async with httpx.AsyncClient(transport=transport, base_url="http://w") as client:
            return (await client.get(path)).status_code

    assert asyncio.run(probe("/healthz")) == 200
    assert asyncio.run(probe("/metrics")) == 200
    assert asyncio.run(probe("/api/v1/jobs")) == 404  # no API on a worker
    assert started["kwargs"]["timeout_graceful_shutdown"] == 5
