"""Recorded model responses, so the eval suite runs deterministically without an API key.

A recording is the ordered list of every model call one fixture's pipeline run made: which
agent asked, a fingerprint of exactly what it asked (system prompt, user prompt, output schema),
and the structured answer and usage that came back. Replaying it serves those answers in order
and refuses a call whose fingerprint differs: a changed prompt, schema or upstream output means
the recording no longer describes this code and must be refreshed (``autoswe eval record``).

Every recording says where it came from (``source``). The ones checked into ``evals/recordings``
were produced by the deterministic offline provider and say so; they are not Claude output.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from autoswe import __version__
from autoswe.llm.base import LLMError, LLMProvider, LLMRequest, LLMResponse, TokenUsage

FORMAT_VERSION = 1
Source = Literal["offline-provider", "anthropic"]
PROVENANCE = {
    "offline-provider": (
        "Synthesized by AutoSWE's deterministic offline provider (heuristics and templates). "
        "This is NOT output from Claude or any other model; it exercises the harness and pins "
        "the offline pipeline's behaviour. Re-record with --source anthropic for model output."
    ),
    "anthropic": "Recorded from the Anthropic Messages API (see `model`).",
}


class RecordingMismatchError(LLMError):
    """The pipeline asked for something the recording does not contain."""


class RecordedCall(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agent: str
    request_sha256: str
    model: str
    usage: dict[str, int] = Field(default_factory=dict)
    output: dict[str, Any]


class Recording(BaseModel):
    model_config = ConfigDict(extra="forbid")

    format_version: int = FORMAT_VERSION
    fixture: str
    source: Source
    provenance: str
    model: str
    recorded_at: datetime
    autoswe_version: str
    sandbox: str = Field(description="Sandbox backend active while recording")
    calls: list[RecordedCall]

    @classmethod
    def load(cls, path: Path) -> Recording:
        recording = cls.model_validate_json(path.read_text(encoding="utf-8"))
        if recording.format_version != FORMAT_VERSION:
            raise ValueError(f"{path}: unsupported recording format {recording.format_version}")
        return recording

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        text = json.dumps(self.model_dump(mode="json"), indent=1, sort_keys=False) + "\n"
        path.write_text(text, encoding="utf-8", newline="\n")


def fingerprint(request: LLMRequest[Any]) -> str:
    """What was asked, independent of provider: agent, prompts and the output schema."""
    payload = json.dumps(
        {
            "agent": request.agent,
            "system": request.system,
            "prompt": request.prompt,
            "schema": request.output_type.model_json_schema(),
        },
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def _usage_dict(usage: TokenUsage) -> dict[str, int]:
    return {key: int(value) for key, value in dataclasses.asdict(usage).items() if value}


def _usage_from(values: dict[str, int]) -> TokenUsage:
    known = {f.name for f in dataclasses.fields(TokenUsage)}
    return TokenUsage(**{key: value for key, value in values.items() if key in known})


class RecordingProvider:
    """Wraps a real provider and keeps every call for :meth:`recording`."""

    def __init__(self, inner: LLMProvider) -> None:
        self._inner = inner
        self.name = inner.name
        self.model = inner.model
        self.calls: list[RecordedCall] = []

    async def generate[T: BaseModel](self, request: LLMRequest[T]) -> LLMResponse[T]:
        response = await self._inner.generate(request)
        self.calls.append(
            RecordedCall(
                agent=request.agent,
                request_sha256=fingerprint(request),
                model=response.model,
                usage=_usage_dict(response.usage),
                output=response.output.model_dump(mode="json"),
            )
        )
        return response

    def recording(self, fixture: str, source: Source, sandbox: str) -> Recording:
        return Recording(
            fixture=fixture,
            source=source,
            provenance=PROVENANCE[source],
            model=self.model,
            recorded_at=datetime.now(UTC).replace(microsecond=0),
            autoswe_version=__version__,
            sandbox=sandbox,
            calls=list(self.calls),
        )


class ReplayProvider:
    """Serves a recording's answers in order; a call that does not match is an error."""

    name = "replay"

    def __init__(self, recording: Recording) -> None:
        self.recording = recording
        self.model = recording.model
        self._next = 0

    @property
    def unused_calls(self) -> int:
        return len(self.recording.calls) - self._next

    async def generate[T: BaseModel](self, request: LLMRequest[T]) -> LLMResponse[T]:
        fixture = self.recording.fixture
        if self._next >= len(self.recording.calls):
            raise RecordingMismatchError(
                f"recording '{fixture}' has no call #{self._next + 1} ({request.agent}); "
                "re-record with `autoswe eval record`"
            )
        call = self.recording.calls[self._next]
        self._next += 1
        if call.agent != request.agent:
            raise RecordingMismatchError(
                f"recording '{fixture}' call #{self._next} is {call.agent}, "
                f"the pipeline asked {request.agent}; re-record"
            )
        if call.request_sha256 != fingerprint(request):
            raise RecordingMismatchError(
                f"recording '{fixture}' call #{self._next} ({request.agent}) was made for a "
                "different prompt or schema; re-record with `autoswe eval record`"
            )
        output = request.output_type.model_validate(call.output)
        return LLMResponse(output=output, model=call.model, usage=_usage_from(call.usage))
