"""Hermes ProviderProfile registration for the Claude ACP bridge."""

from __future__ import annotations

import os

from .models import DEFAULT_MODEL, FALLBACK_MODELS, max_output_tokens_for

DEFAULT_HOST = os.getenv("HERMES_CLAUDE_ACP_HOST", "127.0.0.1")
DEFAULT_PORT = int(os.getenv("HERMES_CLAUDE_ACP_PORT", "3457"))
DEFAULT_BASE_URL = os.getenv(
    "HERMES_CLAUDE_ACP_BASE_URL",
    f"http://{DEFAULT_HOST}:{DEFAULT_PORT}/v1",
)


def register_provider_profile() -> None:
    """Register the provider with Hermes when Hermes modules are available."""
    from providers import register_provider
    from providers.base import ProviderProfile

    class ClaudeACPProfile(ProviderProfile):
        """Claude ACP profile with per-model output limits."""

        def get_max_tokens(self, model: str | None) -> int:
            return max_output_tokens_for(model)

    profile = ClaudeACPProfile(
        name="claude-acp",
        aliases=(
            "claude-agent-acp",
            "claude-code-acp",
            "anthropic-acp",
        ),
        display_name="Claude ACP",
        description="Claude Code through Agent Client Protocol via a local bridge",
        api_mode="chat_completions",
        env_vars=(),
        base_url=DEFAULT_BASE_URL,
        auth_type="api_key",
        supports_health_check=False,
        fallback_models=FALLBACK_MODELS,
        default_max_tokens=max_output_tokens_for(DEFAULT_MODEL),
        default_aux_model="claude-haiku-4-5",
    )
    register_provider(profile)


# Provider plugins are imported for side effects by Hermes provider discovery.
try:
    register_provider_profile()
except Exception:
    # Importing outside Hermes, for tests or packaging, should not fail simply
    # because Hermes' provider modules are absent from sys.path.
    pass
