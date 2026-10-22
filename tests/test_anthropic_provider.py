"""AnthropicProvider request construction and response handling, against a fake SDK client."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast

import anthropic
import httpx2
import pytest

from autoswe.llm.anthropic_provider import FALLBACK_BETA, AnthropicProvider
from autoswe.llm.base import (
    LLMConfigurationError,
    LLMOutputError,
    LLMRefusalError,
    LLMRequest,
    LLMTransientError,
)
from autoswe.llm.registry import build_registry
from autoswe.pipeline.artifacts import ReviewReport
from tests.conftest import make_settings

VALID = '{"summary": "ok", "score": 91, "findings": []}'


def _message(text: str = VALID, stop_reason: str = "end_turn", **extra: Any) -> SimpleNamespace:
    return SimpleNamespace(
        model="claude-opus-5-5",
        stop_reason=stop_reason,
        content=[
            SimpleNamespace(type="thinking", thinking=""),
            SimpleNamespace(type="text", text=text),
        ],
        usage=SimpleNamespace(
            input_tokens=100, output_tokens=40, cache_read_input_tokens=900,
            cache_creation_input_tokens=0,
        ),
        **extra,
    )  # fmt: skip


class _Stream:
    def __init__(self, outcome: Any) -> None:
        self._outcome = outcome

    async def __aenter__(self) -> _Stream:
        if isinstance(self._outcome, BaseException):
            raise self._outcome
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    async def get_final_message(self) -> Any:
        return self._outcome


class FakeClient:
    def __init__(self, outcome: Any) -> None:
        self.calls: list[dict[str, Any]] = []
        outer = self

        class _Messages:
            def stream(self, **kwargs: Any) -> _Stream:
                outer.calls.append(kwargs)
                return _Stream(outcome)

        self.beta = SimpleNamespace(messages=_Messages())


def _provider(outcome: Any, **kwargs: Any) -> tuple[AnthropicProvider, FakeClient]:
    client = FakeClient(outcome)
    provider = AnthropicProvider(
        model="claude-opus-5-5",
        max_tokens=64_000,
        client=cast(anthropic.AsyncAnthropic, client),
        **kwargs,
    )
    return provider, client


REQUEST = LLMRequest(
    agent="reviewer",
    system="You are a reviewer.",
    prompt="<files>...</files>",
    output_type=ReviewReport,
    effort="medium",
)


async def test_request_uses_structured_output_adaptive_thinking_and_fallbacks() -> None:
    provider, client = _provider(_message())
    response = await provider.generate(REQUEST)

    assert response.output == ReviewReport(summary="ok", score=91, findings=[])
    assert response.model == "claude-opus-5-5"
    assert response.usage.input_tokens == 1000  # uncached + cache reads + cache writes
    assert response.usage.output_tokens == 40

    (params,) = client.calls
    assert params["model"] == "claude-opus-5-5"
    assert params["max_tokens"] == 64_000
    assert params["thinking"] == {"type": "adaptive"}
    assert params["output_config"]["effort"] == "medium"
    schema = params["output_config"]["format"]
    assert schema["type"] == "json_schema"
    assert schema["schema"]["additionalProperties"] is False
    assert set(schema["schema"]["required"]) == {"summary", "score", "findings"}
    assert params["system"] == [
        {"type": "text", "text": "You are a reviewer.", "cache_control": {"type": "ephemeral"}}
    ]
    assert params["messages"] == [{"role": "user", "content": "<files>...</files>"}]
    assert params["betas"] == [FALLBACK_BETA]
    assert params["fallbacks"] == "default"
    for removed in ("temperature", "top_p", "top_k", "budget_tokens"):
        assert removed not in params


async def test_effort_override_and_disabled_fallbacks() -> None:
    provider, client = _provider(_message(), effort_override="high", server_fallbacks=False)
    await provider.generate(REQUEST)
    (params,) = client.calls
    assert params["output_config"]["effort"] == "high"
    assert "fallbacks" not in params and "betas" not in params


async def test_refusal_is_reported_before_reading_content() -> None:
    details = SimpleNamespace(category="cyber", explanation="...")
    provider, _ = _provider(_message(text="", stop_reason="refusal", stop_details=details))
    with pytest.raises(LLMRefusalError, match="category=cyber"):
        await provider.generate(REQUEST)


async def test_truncated_output_is_an_output_error() -> None:
    provider, _ = _provider(_message(text='{"summary": "o', stop_reason="max_tokens"))
    with pytest.raises(LLMOutputError, match="max_tokens"):
        await provider.generate(REQUEST)


@pytest.mark.parametrize("text", ["not json", '{"summary": "ok"}', '{"summary": 1, "score": "x"}'])
async def test_schema_mismatch_is_an_output_error(text: str) -> None:
    provider, _ = _provider(_message(text=text))
    with pytest.raises(LLMOutputError, match="did not match ReviewReport"):
        await provider.generate(REQUEST)


def _status_error(cls: type[anthropic.APIStatusError], code: int) -> anthropic.APIStatusError:
    request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    return cls("error", response=httpx2.Response(code, request=request), body=None)


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (_status_error(anthropic.AuthenticationError, 401), LLMConfigurationError),
        (_status_error(anthropic.PermissionDeniedError, 403), LLMConfigurationError),
        (_status_error(anthropic.NotFoundError, 404), LLMConfigurationError),
        (_status_error(anthropic.BadRequestError, 400), LLMConfigurationError),
        (_status_error(anthropic.RateLimitError, 429), LLMTransientError),
        (_status_error(anthropic.InternalServerError, 529), LLMTransientError),
        (
            anthropic.APIConnectionError(request=httpx2.Request("POST", "https://x")),
            LLMTransientError,
        ),
    ],
)
async def test_sdk_errors_are_mapped(error: Exception, expected: type[Exception]) -> None:
    provider, _ = _provider(error)
    with pytest.raises(expected) as raised:
        await provider.generate(REQUEST)
    assert raised.value.__cause__ is error
    assert raised.value.retryable is (expected is LLMTransientError)  # type: ignore[attr-defined]


def test_registry_selects_anthropic_only_when_configured(tmp_path: Any) -> None:
    offline_only = build_registry(make_settings(tmp_path))
    assert offline_only.default == "offline"
    assert [p.name for p in offline_only.describe()] == ["offline"]

    with_key = build_registry(make_settings(tmp_path, anthropic_api_key="sk-test"))
    assert with_key.default == "anthropic"
    assert with_key.get("anthropic").model == "claude-opus-5-5"
    assert with_key.get("offline").name == "offline"

    pinned = build_registry(
        make_settings(tmp_path, anthropic_api_key="sk-test", llm_default_provider="offline")
    )
    assert pinned.default == "offline"

    with pytest.raises(ValueError, match="not configured"):
        build_registry(make_settings(tmp_path, llm_default_provider="anthropic"))
