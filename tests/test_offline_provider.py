"""Offline provider pacing (OFFLINE_DELAY_S), used to make demo runs watchable."""

from __future__ import annotations

import pytest

from autoswe.config import Settings
from autoswe.llm.offline import OfflineProvider
from autoswe.llm.registry import build_registry
from autoswe.pipeline.agents import PipelineState, default_agents
from tests.conftest import LIBRARY_SPEC


async def test_delay_paces_every_call_without_changing_the_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    slept: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        slept.append(seconds)

    monkeypatch.setattr("autoswe.llm.offline.asyncio.sleep", fake_sleep)
    state = PipelineState(spec=LIBRARY_SPEC)

    async def emit(_: str) -> None:
        return None

    for agent in default_agents():
        await agent.run(state, OfflineProvider(delay_s=1.5), emit)
    assert len(slept) == 5
    assert set(slept) == {1.5}
    assert state.review is not None
    assert state.review.score == 100


async def test_no_delay_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    async def forbidden(_: float) -> None:
        raise AssertionError("the default offline provider must not sleep")

    monkeypatch.setattr("autoswe.llm.offline.asyncio.sleep", forbidden)
    state = PipelineState(spec=LIBRARY_SPEC)

    async def emit(_: str) -> None:
        return None

    for agent in default_agents():
        await agent.run(state, OfflineProvider(), emit)
    assert state.validation is not None
    assert state.validation.passed


def test_registry_passes_the_configured_delay() -> None:
    registry = build_registry(Settings(auth_disabled=True, offline_delay_s=0.25))
    provider = registry.get("offline")
    assert isinstance(provider, OfflineProvider)
    assert provider.delay_s == 0.25


def test_delay_is_bounded() -> None:
    with pytest.raises(ValueError, match="offline_delay_s"):
        Settings(auth_disabled=True, offline_delay_s=60)
