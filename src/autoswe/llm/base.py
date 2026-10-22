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


@dataclass(frozen=True, slots=True)
class TokenUsage:
    input_tokens: int = 0
    output_tokens: int = 0

    def __add__(self, other: TokenUsage) -> TokenUsage:
        return TokenUsage(
            self.input_tokens + other.input_tokens, self.output_tokens + other.output_tokens
        )


@dataclass(frozen=True, slots=True)
class LLMResponse[T: BaseModel]:
    output: T
    model: str
    usage: TokenUsage = TokenUsage()


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
