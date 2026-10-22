from pathlib import Path

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
