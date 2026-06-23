"""Claude model catalog for the Hermes Claude ACP provider."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ClaudeModelSpec:
    id: str
    display_name: str
    description: str
    context_length: int
    max_output_tokens: int
    aliases: tuple[str, ...] = ()
    default: bool = False

    def as_openai_model(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "object": "model",
            "owned_by": "claude-acp",
            "name": self.display_name,
            "description": self.description,
            "context_length": self.context_length,
            "context_window": self.context_length,
            "max_input_tokens": self.context_length,
            "max_tokens": self.max_output_tokens,
            "max_output_tokens": self.max_output_tokens,
            "max_completion_tokens": self.max_output_tokens,
            "aliases": list(self.aliases),
        }


# Source: https://platform.claude.com/docs/en/about-claude/models/overview
CLAUDE_MODELS: tuple[ClaudeModelSpec, ...] = (
    ClaudeModelSpec(
        id="claude-opus-4-8",
        display_name="Claude Opus 4.8",
        description="Most capable Opus-tier model for complex reasoning and agentic coding",
        context_length=1_000_000,
        max_output_tokens=128_000,
    ),
    ClaudeModelSpec(
        id="claude-sonnet-4-6",
        display_name="Claude Sonnet 4.6",
        description="Best combination of speed and intelligence",
        context_length=1_000_000,
        max_output_tokens=128_000,
        default=True,
    ),
    ClaudeModelSpec(
        id="claude-haiku-4-5",
        display_name="Claude Haiku 4.5",
        description="Fastest model with near-frontier intelligence",
        context_length=200_000,
        max_output_tokens=64_000,
        aliases=("claude-haiku-4-5-20251001",),
    ),
)

DEFAULT_MODEL = next((model.id for model in CLAUDE_MODELS if model.default), CLAUDE_MODELS[0].id)
FALLBACK_MODELS = tuple(model.id for model in CLAUDE_MODELS)
MODEL_SPECS = {model.id: model for model in CLAUDE_MODELS}
for _model in CLAUDE_MODELS:
    for _alias in _model.aliases:
        MODEL_SPECS[_alias] = _model


def max_output_tokens_for(model: str | None) -> int:
    if not model:
        return MODEL_SPECS[DEFAULT_MODEL].max_output_tokens

    normalized = model.lower().replace(".", "-")
    direct = MODEL_SPECS.get(normalized)
    if direct:
        return direct.max_output_tokens

    best: ClaudeModelSpec | None = None
    best_key = ""
    for key, spec in MODEL_SPECS.items():
        normalized_key = key.lower().replace(".", "-")
        if normalized_key in normalized and len(normalized_key) > len(best_key):
            best = spec
            best_key = normalized_key
    return (best or MODEL_SPECS[DEFAULT_MODEL]).max_output_tokens


def model_cards() -> list[dict[str, Any]]:
    return [model.as_openai_model() for model in CLAUDE_MODELS]
