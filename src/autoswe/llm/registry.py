"""Build the set of providers a deployment offers, based on configuration."""

from __future__ import annotations

from dataclasses import dataclass

from autoswe.config import Settings
from autoswe.llm.base import LLMProvider
from autoswe.llm.offline import OfflineProvider


class UnknownProviderError(LookupError):
    pass


@dataclass(frozen=True, slots=True)
class ProviderInfo:
    name: str
    model: str
    default: bool


class ProviderRegistry:
    def __init__(self, providers: dict[str, LLMProvider], default: str) -> None:
        if default not in providers:
            raise ValueError(f"default provider '{default}' is not configured")
        self._providers = providers
        self.default = default

    def get(self, name: str | None = None) -> LLMProvider:
        key = name or self.default
        try:
            return self._providers[key]
        except KeyError:
            raise UnknownProviderError(key) from None

    def describe(self) -> list[ProviderInfo]:
        return [
            ProviderInfo(name=name, model=provider.model, default=name == self.default)
            for name, provider in self._providers.items()
        ]


def build_registry(settings: Settings) -> ProviderRegistry:
    providers: dict[str, LLMProvider] = {"offline": OfflineProvider(settings.offline_delay_s)}
    if settings.anthropic_configured:
        from autoswe.llm.anthropic_provider import AnthropicProvider  # noqa: PLC0415

        providers["anthropic"] = AnthropicProvider.from_settings(settings)

    default = settings.llm_default_provider
    if default == "auto":
        default = "anthropic" if "anthropic" in providers else "offline"
    elif default not in providers:
        raise ValueError(
            f"LLM_DEFAULT_PROVIDER={default} but that provider is not configured "
            "(set ANTHROPIC_API_KEY)"
        )
    return ProviderRegistry(providers, default)
