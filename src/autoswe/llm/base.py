"""Provider-agnostic LLM interface used by every pipeline agent.

Agents never talk to an SDK directly. They build an :class:`LLMRequest` describing the task
(system prompt, user prompt, the Pydantic type the answer must conform to, and the structured
inputs it was derived from) and hand it to whichever :class:`LLMProvider` the job selected.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel

from autoswe.config import Effort


@dataclass(frozen=True, slots=True)
class LLMRequest[T: BaseModel]:
    agent: str
    system: str
    prompt: str
    output_type: type[T]
    # Structured inputs the prompt was rendered from. Remote providers ignore it (the prompt
    # already contains everything); the offline provider uses it instead of parsing prose.
    context: Mapping[str, Any] = field(default_factory=dict)
    effort: Effort = "high"
    # Facts about the call for tracing (e.g. which repair round it is); never sent to a model.
    metadata: Mapping[str, str | int | bool] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class TokenUsage:
    """Billed tokens, and their cost once the metering layer has priced them.

    ``input_tokens`` is the total input, cache reads and cache writes **included** (the same
    convention as OpenTelemetry's ``gen_ai.usage.input_tokens``); the two cache fields say how
    much of it was read from or written to the prompt cache. Providers leave the cost fields at
    zero: :class:`autoswe.metering.MeteredProvider` prices each call from the pricing table.
    """

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cost_microusd: int = 0  # millionths of a US dollar
    unpriced_calls: int = 0  # calls whose model is not in the pricing table

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    @property
    def uncached_input_tokens(self) -> int:
        return self.input_tokens - self.cache_read_input_tokens - self.cache_creation_input_tokens

    def __add__(self, other: TokenUsage) -> TokenUsage:
        return TokenUsage(*(a + b for a, b in zip(_values(self), _values(other), strict=True)))

    def __sub__(self, other: TokenUsage) -> TokenUsage:
        return TokenUsage(*(a - b for a, b in zip(_values(self), _values(other), strict=True)))


def _values(usage: TokenUsage) -> tuple[int, ...]:
    return (
        usage.input_tokens,
        usage.output_tokens,
        usage.cache_read_input_tokens,
        usage.cache_creation_input_tokens,
        usage.cost_microusd,
        usage.unpriced_calls,
    )


@dataclass(frozen=True, slots=True)
class LLMResponse[T: BaseModel]:
    output: T
    model: str
    usage: TokenUsage = TokenUsage()
    # Usage per billed attempt and the model that served it. A server-side fallback bills the
    # declined attempt too, possibly on another model; empty means "all of `usage` on `model`".
    billed: tuple[tuple[str, TokenUsage], ...] = ()


class LLMError(Exception):
    """Base class for provider failures surfaced to the pipeline."""

    retryable: bool = False


class LLMTransientError(LLMError):
    """Rate limits, timeouts, 5xx: the SDK already retried, the job may be resubmitted."""

    retryable = True


class LLMConfigurationError(LLMError):
    """Bad credentials, unknown model, malformed request."""


class LLMRefusalError(LLMError):
    """The model declined the request."""


class LLMOutputError(LLMError):
    """The model answered, but not with output that matches the requested schema."""


@runtime_checkable
class LLMProvider(Protocol):
    name: str
    model: str

    async def generate[T: BaseModel](self, request: LLMRequest[T]) -> LLMResponse[T]: ...


def is_metered(provider: object) -> bool:
    """Whether calls cost tokens (and so count against budgets). Providers opt out with
    ``metered = False``, as the offline provider does."""
    return bool(getattr(provider, "metered", True))
