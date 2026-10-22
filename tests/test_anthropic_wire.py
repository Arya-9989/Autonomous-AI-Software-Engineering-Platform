"""AnthropicProvider against the real ``anthropic`` SDK, with only the HTTP transport faked.

``test_anthropic_provider.py`` checks the provider's own logic with a stand-in client. These
tests go one layer lower: the official ``AsyncAnthropic`` client builds and sends the request,
then parses a Server-Sent Events body shaped like the documented Messages streaming protocol
(``message_start``, ``content_block_*``, ``message_delta``, ``message_stop`` and ``error``
events). That pins down what the SDK actually puts on the wire for our parameters (beta header,
``fallbacks``, ``output_config``, adaptive thinking, the cacheable system block) and how the
accumulated message looks when it reaches our parsing code.

What this cannot prove: that the live API accepts this exact request today, or that its
responses keep these shapes. No call to api.anthropic.com is made anywhere in the test suite.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

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
from autoswe.pipeline.artifacts import ReviewReport

MODEL = "claude-opus-5-5"
ANSWER = {"summary": "Solid service.", "score": 88, "findings": []}

REQUEST = LLMRequest(
    agent="reviewer",
    system="You review generated projects.",
    prompt="<files>...</files>",
    output_type=ReviewReport,
    effort="medium",
)


def _event(name: str, data: dict[str, Any]) -> str:
    return f"event: {name}\ndata: {json.dumps(data)}\n\n"


def _message_start(model: str = MODEL, **usage: int) -> str:
    return _event(
        "message_start",
        {
            "type": "message_start",
            "message": {
                "id": "msg_test",
                "type": "message",
                "role": "assistant",
                "model": model,
                "content": [],
                "stop_reason": None,
                "stop_sequence": None,
                "usage": {
                    "input_tokens": 120,
                    "output_tokens": 1,
                    "cache_creation_input_tokens": 0,
                    "cache_read_input_tokens": 0,
                    **usage,
                },
            },
        },
    )


def _thinking_block(index: int) -> str:
    # Opus 5.5 omits thinking text by default: an empty thinking block with a signature.
    return (
        _event(
            "content_block_start",
            {
                "type": "content_block_start",
                "index": index,
                "content_block": {"type": "thinking", "thinking": "", "signature": ""},
            },
        )
        + _event(
            "content_block_delta",
            {
                "type": "content_block_delta",
                "index": index,
                "delta": {"type": "signature_delta", "signature": "c2lnbmF0dXJl"},
            },
        )
        + _event("content_block_stop", {"type": "content_block_stop", "index": index})
    )


def _text_block(index: int, text: str, chunks: int = 3) -> str:
    size = max(1, len(text) // chunks)
    pieces = [text[i : i + size] for i in range(0, len(text), size)]
    body = _event(
        "content_block_start",
        {
            "type": "content_block_start",
            "index": index,
            "content_block": {"type": "text", "text": ""},
        },
    )
    for piece in pieces:
        body += _event(
            "content_block_delta",
            {
                "type": "content_block_delta",
                "index": index,
                "delta": {"type": "text_delta", "text": piece},
            },
        )
    return body + _event("content_block_stop", {"type": "content_block_stop", "index": index})


def _fallback_block(index: int, source: str, target: str) -> str:
    return _event(
        "content_block_start",
        {
            "type": "content_block_start",
            "index": index,
            "content_block": {
                "type": "fallback",
                "from": {"model": source},
                "to": {"model": target},
            },
        },
    ) + _event("content_block_stop", {"type": "content_block_stop", "index": index})


def _message_end(
    stop_reason: str = "end_turn",
    output_tokens: int = 40,
    stop_details: dict[str, Any] | None = None,
    **usage: Any,
) -> str:
    return _event(
        "message_delta",
        {
            "type": "message_delta",
            "delta": {
                "stop_reason": stop_reason,
                "stop_sequence": None,
                "stop_details": stop_details,
            },
            "usage": {"output_tokens": output_tokens, **usage},
        },
    ) + _event("message_stop", {"type": "message_stop"})


class Transport:
    """Serves one canned SSE body per request and records what the SDK sent."""

    def __init__(self, *bodies: str | Callable[[httpx2.Request], httpx2.Response]) -> None:
        self._bodies = list(bodies)
        self.requests: list[httpx2.Request] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        body = self._bodies.pop(0)
        if callable(body):
            return body(request)
        return httpx2.Response(
            200,
            headers={"content-type": "text/event-stream", "request-id": "req_test"},
            content=body.encode(),
        )

    @property
    def sent(self) -> dict[str, Any]:
        (request,) = self.requests
        return json.loads(request.content)  # type: ignore[no-any-return]


def _provider(transport: Transport, **kwargs: Any) -> AnthropicProvider:
    client = anthropic.AsyncAnthropic(
        api_key="sk-ant-test-key",
        max_retries=0,
        http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(transport)),
    )
    return AnthropicProvider(model=MODEL, max_tokens=64_000, client=client, **kwargs)


def _success_body(text: str = json.dumps(ANSWER)) -> str:
    return (
        _message_start(cache_read_input_tokens=900, cache_creation_input_tokens=30)
        + _thinking_block(0)
        + _text_block(1, text)
        + _message_end(output_tokens=40)
    )


async def test_wire_request_matches_the_messages_api_contract() -> None:
    transport = Transport(_success_body())
    await _provider(transport).generate(REQUEST)

    (request,) = transport.requests
    assert request.method == "POST"
    assert request.url.path == "/v1/messages"
    assert request.url.params.get("beta") == "true"  # the beta namespace
    assert request.headers["anthropic-beta"] == FALLBACK_BETA == "server-side-fallback-2026-07-01"
    assert request.headers["x-api-key"] == "sk-ant-test-key"
    assert "anthropic-version" in request.headers

    body = transport.sent
    assert body["model"] == MODEL
    assert body["stream"] is True
    assert body["max_tokens"] == 64_000
    assert body["fallbacks"] == "default"
    assert body["thinking"] == {"type": "adaptive"}
    assert body["output_config"]["effort"] == "medium"
    fmt = body["output_config"]["format"]
    assert fmt["type"] == "json_schema"
    assert fmt["schema"]["type"] == "object"
    assert fmt["schema"]["additionalProperties"] is False
    assert body["system"] == [
        {
            "type": "text",
            "text": "You review generated projects.",
            "cache_control": {"type": "ephemeral"},
        }
    ]
    assert body["messages"] == [{"role": "user", "content": "<files>...</files>"}]
    # Removed on current models (400 if sent): sampling parameters and thinking budgets.
    for key in ("temperature", "top_p", "top_k", "betas"):
        assert key not in body
    assert "budget_tokens" not in body["thinking"]


async def test_streamed_answer_is_parsed_and_every_input_token_counted() -> None:
    response = await _provider(Transport(_success_body())).generate(REQUEST)

    assert response.output == ReviewReport.model_validate(ANSWER)
    assert response.model == MODEL
    # Total input = uncached + cache reads + cache writes (the Messages API usage contract).
    assert response.usage.input_tokens == 120 + 900 + 30
    assert response.usage.output_tokens == 40


async def test_fallbacks_disabled_sends_no_beta_header_or_parameter() -> None:
    transport = Transport(_success_body())
    await _provider(transport, server_fallbacks=False).generate(REQUEST)
    assert "anthropic-beta" not in transport.requests[0].headers
    assert "fallbacks" not in transport.sent


async def test_pre_output_fallback_is_transparent() -> None:
    # A decline before any output: the fallback block comes first, the fallback model answers.
    body = (
        _message_start(model="claude-opus-4-8")
        + _fallback_block(0, MODEL, "claude-opus-4-8")
        + _text_block(1, json.dumps(ANSWER))
        + _message_end()
    )
    response = await _provider(Transport(body)).generate(REQUEST)
    assert response.output.score == 88
    assert response.model == "claude-opus-4-8"


async def test_mid_stream_fallback_continues_the_partial_answer() -> None:
    # Streaming keeps the declined model's partial text, marks the boundary with a fallback
    # block, and the fallback model continues from that text.
    text = json.dumps(ANSWER)
    cut = len(text) // 2
    body = (
        _message_start()
        + _thinking_block(0)
        + _text_block(1, text[:cut])
        + _fallback_block(2, MODEL, "claude-opus-4-8")
        + _text_block(3, text[cut:])
        + _message_end()
    )
    response = await _provider(Transport(body)).generate(REQUEST)
    assert response.output == ReviewReport.model_validate(ANSWER)


async def test_fallback_usage_counts_every_attempt() -> None:
    # Top-level usage covers only the attempt that produced the message; usage.iterations
    # is the per-attempt record (the declined attempt is billed too).
    iterations = [
        {"type": "message", "model": MODEL, "input_tokens": 120, "output_tokens": 15,
         "cache_creation_input_tokens": 0, "cache_read_input_tokens": 900},
        {"type": "fallback_message", "model": "claude-opus-4-8", "input_tokens": 1020,
         "output_tokens": 40, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0},
    ]  # fmt: skip
    body = (
        _message_start(model="claude-opus-4-8", input_tokens=1020)
        + _fallback_block(0, MODEL, "claude-opus-4-8")
        + _text_block(1, json.dumps(ANSWER))
        + _message_end(output_tokens=40, iterations=iterations)
    )
    response = await _provider(Transport(body)).generate(REQUEST)
    assert response.usage.input_tokens == 120 + 900 + 1020
    assert response.usage.output_tokens == 15 + 40


async def test_refusal_reports_the_category_from_stop_details() -> None:
    body = (
        _message_start()
        + _text_block(0, '{"summary": "par')  # partial output, then the classifier stops it
        + _message_end(
            stop_reason="refusal",
            stop_details={"type": "refusal", "category": "cyber", "explanation": "Declined."},
        )
    )
    with pytest.raises(LLMRefusalError, match="category=cyber"):
        await _provider(Transport(body)).generate(REQUEST)


async def test_refusal_without_details_is_still_a_refusal() -> None:
    body = _message_start() + _message_end(stop_reason="refusal", output_tokens=0)
    with pytest.raises(LLMRefusalError, match="category=None"):
        await _provider(Transport(body)).generate(REQUEST)


async def test_max_tokens_is_reported_as_truncation() -> None:
    body = _message_start() + _text_block(0, '{"summary": "o') + _message_end("max_tokens")
    with pytest.raises(LLMOutputError, match="max_tokens"):
        await _provider(Transport(body)).generate(REQUEST)


async def test_context_window_exhaustion_is_reported_as_such() -> None:
    body = (
        _message_start()
        + _text_block(0, '{"summary": "o')
        + _message_end("model_context_window_exceeded")
    )
    with pytest.raises(LLMOutputError, match="context window"):
        await _provider(Transport(body)).generate(REQUEST)


@pytest.mark.parametrize("error_type", ["overloaded_error", "api_error", "rate_limit_error"])
async def test_mid_stream_server_errors_are_transient(error_type: str) -> None:
    # The SDK raises an APIStatusError carrying the stream's HTTP status (200) for an SSE
    # `error` event, so the error type in the body decides retryability.
    body = (
        _message_start()
        + _text_block(0, '{"summary"')
        + _event("error", {"type": "error", "error": {"type": error_type, "message": "busy"}})
    )
    with pytest.raises(LLMTransientError) as raised:
        await _provider(Transport(body)).generate(REQUEST)
    assert raised.value.retryable


async def test_mid_stream_request_errors_are_not_retryable() -> None:
    body = _message_start() + _event(
        "error",
        {"type": "error", "error": {"type": "invalid_request_error", "message": "bad schema"}},
    )
    with pytest.raises(LLMConfigurationError, match="invalid_request_error"):
        await _provider(Transport(body)).generate(REQUEST)


async def test_http_overloaded_status_is_transient() -> None:
    def overloaded(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(
            529, json={"type": "error", "error": {"type": "overloaded_error", "message": "busy"}}
        )

    with pytest.raises(LLMTransientError):
        await _provider(Transport(overloaded)).generate(REQUEST)
