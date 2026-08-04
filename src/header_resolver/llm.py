"""Provider-agnostic LLM access for Layer 4.

The platform decision (Amazon Bedrock vs the direct Anthropic API) is still
open, so nothing above this module knows which is in use. Both providers speak
the same Messages API; only client construction and the model-ID prefix differ.

    direct    Anthropic()                      model="claude-opus-5"
    bedrock   AnthropicBedrockMantle(region)   model="anthropic.claude-opus-5"

`MockLLMClient` exists so the test suite runs offline with no credentials.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Protocol

DEFAULT_MODEL = "claude-opus-5"
DEFAULT_MAX_TOKENS = 16000


class LLMError(RuntimeError):
    """Raised when the model could not produce a usable answer."""


class LLMClient(Protocol):
    """Minimal surface Layer 4 needs: prompt in, schema-validated JSON out."""

    def complete_json(
        self, system: str, user: str, schema: dict[str, Any]
    ) -> dict[str, Any]:
        ...


@dataclass
class LLMUsage:
    """Per-call token accounting, so Layer 4 cost stays observable."""

    input_tokens: int = 0
    output_tokens: int = 0
    calls: int = 0

    def add(self, usage: Any) -> None:
        self.calls += 1
        self.input_tokens += getattr(usage, "input_tokens", 0) or 0
        self.output_tokens += getattr(usage, "output_tokens", 0) or 0

    def to_dict(self) -> dict[str, int]:
        return {
            "calls": self.calls,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
        }


@dataclass
class AnthropicClient:
    """Direct Anthropic API, or Amazon Bedrock via `provider="bedrock"`.

    Structured outputs are used rather than prose parsing, so a malformed
    mapping is impossible by construction. Adaptive thinking is on because
    Layer 4 only ever sees the cases Layers 0-2 could not settle — by
    definition the ones that need reasoning. `effort` is the cost lever.
    """

    provider: str = "direct"          # "direct" | "bedrock"
    model: str = DEFAULT_MODEL
    aws_region: str | None = None
    effort: str = "high"              # low | medium | high | xhigh | max
    max_tokens: int = DEFAULT_MAX_TOKENS
    usage: LLMUsage = field(default_factory=LLMUsage)
    _client: Any = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if self.provider == "bedrock":
            from anthropic import AnthropicBedrockMantle

            if not self.aws_region:
                raise ValueError("aws_region is required for provider='bedrock'")
            self._client = AnthropicBedrockMantle(aws_region=self.aws_region)
            if not self.model.startswith("anthropic."):
                self.model = f"anthropic.{self.model}"
        elif self.provider == "direct":
            import anthropic

            self._client = anthropic.Anthropic()
        else:
            raise ValueError(f"unknown provider {self.provider!r}")

    def complete_json(
        self, system: str, user: str, schema: dict[str, Any]
    ) -> dict[str, Any]:
        response = self._client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            system=system,
            thinking={"type": "adaptive"},
            output_config={
                "effort": self.effort,
                "format": {"type": "json_schema", "schema": schema},
            },
            messages=[{"role": "user", "content": user}],
        )

        self.usage.add(response.usage)

        # Safety classifiers can decline a request; the response is a normal
        # HTTP 200 with empty or partial content, so stop_reason must be
        # checked before reading content.
        if response.stop_reason == "refusal":
            detail = getattr(response, "stop_details", None)
            category = getattr(detail, "category", None)
            raise LLMError(f"request declined by safety classifiers (category={category})")

        if response.stop_reason == "max_tokens":
            raise LLMError(
                "response hit max_tokens; reduce batch_size or raise max_tokens"
            )

        text = next((b.text for b in response.content if b.type == "text"), None)
        if not text:
            raise LLMError("model returned no text block")

        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:  # pragma: no cover - schema prevents this
            raise LLMError(f"model returned non-JSON despite schema: {exc}") from exc


@dataclass
class MockLLMClient:
    """Deterministic stand-in so Layer 4 is testable without credentials.

    `responses` maps a partner header to the canonical target it should
    resolve to. Anything absent is returned as an abstention, which is the
    behaviour that matters most to test.
    """

    responses: dict[str, str] = field(default_factory=dict)
    confidence: float = 0.9
    calls: list[str] = field(default_factory=list)          # user prompts
    systems: list[str] = field(default_factory=list)        # system prompts

    def complete_json(
        self, system: str, user: str, schema: dict[str, Any]
    ) -> dict[str, Any]:
        self.calls.append(user)
        self.systems.append(system)
        headers = [
            line.split("header:", 1)[1].strip().strip('"')
            for line in user.splitlines()
            if line.strip().startswith("header:")
        ]
        return {
            "mappings": [
                {
                    "source": h,
                    "target": self.responses.get(h),
                    "confidence": self.confidence if h in self.responses else 0.0,
                    "entity": None,
                    "reasoning": "mock",
                }
                for h in headers
            ]
        }
