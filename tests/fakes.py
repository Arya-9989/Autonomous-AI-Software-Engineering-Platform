"""Test doubles for LLM providers."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from pydantic import BaseModel

from autoswe.llm.base import LLMRequest, LLMResponse, TokenUsage
from autoswe.llm.offline import OfflineProvider
from autoswe.llm.registry import ProviderRegistry

Override = Callable[[LLMRequest[Any]], Awaitable[BaseModel] | BaseModel]


class ScriptedProvider:
    """Offline behaviour by default; individual agents can be overridden or paused."""

    name = "scripted"
    model = "scripted-v1"

    def __init__(self, overrides: dict[str, Override] | None = None) -> None:
        self._offline = OfflineProvider()
        self.overrides = overrides or {}
        self.calls: list[str] = []
        self.gates: dict[str, asyncio.Event] = {}
        self.entered: dict[str, asyncio.Event] = {}

    def pause(self, agent: str) -> tuple[asyncio.Event, asyncio.Event]:
        """Block ``agent`` until the returned gate is set; ``entered`` fires when it starts."""
        self.gates[agent] = asyncio.Event()
        self.entered[agent] = asyncio.Event()
        return self.entered[agent], self.gates[agent]

    async def generate[T: BaseModel](self, request: LLMRequest[T]) -> LLMResponse[T]:
        self.calls.append(request.agent)
        if request.agent in self.entered:
            self.entered[request.agent].set()
        if request.agent in self.gates:
            await self.gates[request.agent].wait()
        override = self.overrides.get(request.agent)
        if override is None:
            response = await self._offline.generate(request)
            return LLMResponse(output=response.output, model=self.model, usage=TokenUsage(10, 20))
        result = override(request)
        if isinstance(result, Awaitable):
            result = await result
        assert isinstance(result, request.output_type)
        return LLMResponse(output=result, model=self.model, usage=TokenUsage(10, 20))


def registry_with(provider: Any) -> ProviderRegistry:
    return ProviderRegistry({"offline": OfflineProvider(), provider.name: provider}, provider.name)
