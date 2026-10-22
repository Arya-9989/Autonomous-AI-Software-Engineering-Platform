"""Claude provider built on the official ``anthropic`` SDK.

Each agent call is one streamed Messages request with:

* structured outputs (``output_config.format`` = the agent's Pydantic schema), so the answer is
  JSON that validates against the artifact type;
* adaptive thinking with a per-agent ``effort`` level;
* the stable system prompt marked cacheable;
* streaming + ``get_final_message()`` because file bundles can be large (no HTTP timeouts);
* optional server-side refusal fallbacks (``fallbacks="default"``).
"""

from __future__ import annotations

import dataclasses
import logging
from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from typing import Any

import anthropic
import httpx2
import pydantic
from anthropic import transform_schema
from pydantic import BaseModel

from autoswe.config import Effort, Settings
from autoswe.llm.base import (
    LLMConfigurationError,
    LLMError,
    LLMOutputError,
    LLMRefusalError,
    LLMRequest,
    LLMResponse,
    LLMTransientError,
    TokenUsage,
)

logger = logging.getLogger(__name__)

FALLBACK_BETA = "server-side-fallback-2026-07-01"

# HTTP requests made by the call in progress; the SDK retries inside one call, so this counts
# the original request plus every retry.
_ATTEMPTS: ContextVar[list[int] | None] = ContextVar("anthropic_http_attempts", default=None)


async def _count_attempt(_request: httpx2.Request) -> None:
    counter = _ATTEMPTS.get()
    if counter is not None:
        counter[0] += 1


def http_event_hooks() -> dict[str, list[Callable[[httpx2.Request], Awaitable[None]]]]:
    """Hooks for an ``httpx2.AsyncClient`` passed to the SDK, so retries are visible."""
    return {"request": [_count_attempt]}


class AnthropicProvider:
    name = "anthropic"
    metered = True

    def __init__(
        self,
        *,
        model: str,
        max_tokens: int,
        effort_override: Effort | None = None,
        server_fallbacks: bool = True,
        client: anthropic.AsyncAnthropic | None = None,
        api_key: str | None = None,
        timeout_s: float = 900.0,
        max_retries: int = 2,
    ) -> None:
        self.model = model
        self._max_tokens = max_tokens
        self._effort_override = effort_override
        self._server_fallbacks = server_fallbacks
        self._client = client or anthropic.AsyncAnthropic(
            api_key=api_key,
            timeout=timeout_s,
            max_retries=max_retries,
            http_client=anthropic.DefaultAsyncHttpxClient(event_hooks=http_event_hooks()),
        )

    @classmethod
    def from_settings(cls, settings: Settings) -> AnthropicProvider:
        key = settings.anthropic_api_key.get_secret_value() if settings.anthropic_api_key else None
        return cls(
            model=settings.anthropic_model,
            max_tokens=settings.anthropic_max_tokens,
            effort_override=settings.anthropic_effort,
            server_fallbacks=settings.anthropic_server_fallbacks,
            api_key=key or None,
            timeout_s=settings.anthropic_timeout_s,
            max_retries=settings.anthropic_max_retries,
        )

    def build_params[T: BaseModel](self, request: LLMRequest[T]) -> dict[str, Any]:
        params: dict[str, Any] = {
            "model": self.model,
            "max_tokens": self._max_tokens,
            "system": [
                {"type": "text", "text": request.system, "cache_control": {"type": "ephemeral"}}
            ],
            "messages": [{"role": "user", "content": request.prompt}],
            "thinking": {"type": "adaptive"},
            "output_config": {
                "effort": self._effort_override or request.effort,
                "format": {
                    "type": "json_schema",
                    "schema": transform_schema(request.output_type),
                },
            },
        }
        if self._server_fallbacks:
            params["betas"] = [FALLBACK_BETA]
            params["fallbacks"] = "default"
        return params

    async def generate[T: BaseModel](self, request: LLMRequest[T]) -> LLMResponse[T]:
        counter = [0]
        token = _ATTEMPTS.set(counter)
        try:
            response = await self._generate(request)
        except LLMError as exc:
            exc.http_attempts = counter[0] or None
            raise
        finally:
            _ATTEMPTS.reset(token)
        return dataclasses.replace(response, http_attempts=counter[0] or None)

    async def _generate[T: BaseModel](self, request: LLMRequest[T]) -> LLMResponse[T]:
        params = self.build_params(request)
        try:
            async with self._client.beta.messages.stream(**params) as stream:
                message = await stream.get_final_message()
        except anthropic.AuthenticationError as exc:
            raise LLMConfigurationError("Anthropic rejected the API key") from exc
        except anthropic.PermissionDeniedError as exc:
            raise LLMConfigurationError("API key lacks permission for this model") from exc
        except anthropic.NotFoundError as exc:
            raise LLMConfigurationError(f"Unknown model '{self.model}'") from exc
        except anthropic.BadRequestError as exc:
            raise LLMConfigurationError(f"Request rejected: {exc.message}") from exc
        except anthropic.RateLimitError as exc:
            raise LLMTransientError("Rate limited by the Anthropic API") from exc
        except anthropic.APIStatusError as exc:
            raise _status_error(exc) from exc
        except anthropic.APIConnectionError as exc:  # includes timeouts
            raise LLMTransientError("Could not reach the Anthropic API") from exc

        billed = _billed(message.usage, message.model)
        usage = _usage(message.usage)
        logger.info(
            "llm call finished",
            extra={
                "agent": request.agent,
                "model": message.model,
                "stop_reason": message.stop_reason,
                "input_tokens": usage.input_tokens,
                "output_tokens": usage.output_tokens,
                "request_id": getattr(message, "_request_id", None),
            },
        )

        if message.stop_reason == "refusal":
            details = getattr(message, "stop_details", None)
            category = getattr(details, "category", None) if details else None
            raise LLMRefusalError(f"Model declined the {request.agent} task (category={category})")
        if message.stop_reason == "max_tokens":
            raise LLMOutputError(
                f"{request.agent} output hit max_tokens={self._max_tokens}; raise "
                "ANTHROPIC_MAX_TOKENS or simplify the spec"
            )
        if message.stop_reason == "model_context_window_exceeded":
            raise LLMOutputError(
                f"{request.agent} ran out of the model's context window; simplify the spec"
            )

        text = "".join(block.text for block in message.content if block.type == "text")
        try:
            output = request.output_type.model_validate_json(text)
        except pydantic.ValidationError as exc:
            raise LLMOutputError(
                f"{request.agent} output did not match {request.output_type.__name__}: "
                f"{exc.error_count()} validation error(s)"
            ) from exc
        return LLMResponse(output=output, model=message.model, usage=usage, billed=billed)


# Error types (``error.type`` in an API error body) that are worth resubmitting later.
_TRANSIENT_ERROR_TYPES = frozenset(
    {"overloaded_error", "api_error", "rate_limit_error", "timeout_error"}
)


def _status_error(exc: anthropic.APIStatusError) -> LLMTransientError | LLMConfigurationError:
    """Map an API error the specific handlers did not catch.

    An ``error`` event in the middle of a stream reaches us as an ``APIStatusError`` carrying
    the stream's own HTTP status (200), so the error type in the body decides, then the status.
    """
    body = exc.body if isinstance(exc.body, dict) else {}
    error = body.get("error") if isinstance(body.get("error"), dict) else body
    error_type = error.get("type") if isinstance(error, dict) else None
    if error_type in _TRANSIENT_ERROR_TYPES or exc.status_code >= 500:
        label = error_type or f"HTTP {exc.status_code}"
        return LLMTransientError(f"Anthropic API error ({label}); resubmit the job later")
    label = error_type or f"HTTP {exc.status_code}"
    return LLMConfigurationError(f"Anthropic API error ({label})")


def _usage(usage: Any) -> TokenUsage:
    """Billed tokens for the call; total input = uncached + cache writes + cache reads."""
    total = TokenUsage()
    for _, entry in _billed(usage, ""):
        total += entry
    return total


def _billed(usage: Any, model: str) -> tuple[tuple[str, TokenUsage], ...]:
    """Usage per billed attempt, with the model that served it.

    After a server-side fallback the top-level usage only covers the attempt that produced the
    message, while ``usage.iterations`` lists every attempt (the declined one is billed too), so
    the iterations are used when present.
    """
    iterations = getattr(usage, "iterations", None)
    entries = iterations or [usage]
    billed = []
    for entry in entries:
        cache_read = getattr(entry, "cache_read_input_tokens", 0) or 0
        cache_write = getattr(entry, "cache_creation_input_tokens", 0) or 0
        uncached = getattr(entry, "input_tokens", 0) or 0
        billed.append(
            (
                (getattr(entry, "model", None) if iterations else None) or model,
                TokenUsage(
                    input_tokens=uncached + cache_read + cache_write,
                    output_tokens=getattr(entry, "output_tokens", 0) or 0,
                    cache_read_input_tokens=cache_read,
                    cache_creation_input_tokens=cache_write,
                ),
            )
        )
    return tuple(billed)
