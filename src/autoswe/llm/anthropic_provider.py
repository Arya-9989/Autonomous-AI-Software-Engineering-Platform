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

import logging
from typing import Any

import anthropic
import pydantic
from anthropic import transform_schema
from pydantic import BaseModel

from autoswe.config import Effort, Settings
from autoswe.llm.base import (
    LLMConfigurationError,
    LLMOutputError,
    LLMRefusalError,
    LLMRequest,
    LLMResponse,
    LLMTransientError,
    TokenUsage,
)

logger = logging.getLogger(__name__)

FALLBACK_BETA = "server-side-fallback-2026-07-01"


class AnthropicProvider:
    name = "anthropic"

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
            api_key=api_key, timeout=timeout_s, max_retries=max_retries
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
            if exc.status_code >= 500:
                raise LLMTransientError(f"Anthropic API error {exc.status_code}") from exc
            raise LLMConfigurationError(f"Anthropic API error {exc.status_code}") from exc
        except anthropic.APIConnectionError as exc:  # includes timeouts
            raise LLMTransientError("Could not reach the Anthropic API") from exc

        usage = TokenUsage(
            input_tokens=(message.usage.input_tokens or 0)
            + (getattr(message.usage, "cache_read_input_tokens", 0) or 0)
            + (getattr(message.usage, "cache_creation_input_tokens", 0) or 0),
            output_tokens=message.usage.output_tokens or 0,
        )
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

        text = "".join(block.text for block in message.content if block.type == "text")
        try:
            output = request.output_type.model_validate_json(text)
        except pydantic.ValidationError as exc:
            raise LLMOutputError(
                f"{request.agent} output did not match {request.output_type.__name__}: "
                f"{exc.error_count()} validation error(s)"
            ) from exc
        return LLMResponse(output=output, model=message.model, usage=usage)
