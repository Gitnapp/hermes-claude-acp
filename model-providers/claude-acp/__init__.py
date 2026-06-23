"""User-installable Hermes model-provider profile for Claude ACP."""

from __future__ import annotations

import os
from pathlib import Path
import sys

repo_root = Path(__file__).resolve().parents[2]
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

from providers import register_provider
from providers.base import ProviderProfile
from hermes_claude_acp.models import DEFAULT_MODEL, FALLBACK_MODELS, max_output_tokens_for

host = os.getenv("HERMES_CLAUDE_ACP_HOST", "127.0.0.1")
port = int(os.getenv("HERMES_CLAUDE_ACP_PORT", "3457"))
base_url = os.getenv("HERMES_CLAUDE_ACP_BASE_URL", f"http://{host}:{port}/v1")


class ClaudeACPProfile(ProviderProfile):
    """Claude ACP profile with current Claude model defaults."""

    def get_max_tokens(self, model: str | None) -> int:
        return max_output_tokens_for(model)


claude_acp = ClaudeACPProfile(
    name="claude-acp",
    aliases=("claude-agent-acp", "claude-code-acp", "anthropic-acp"),
    display_name="Claude ACP",
    description="Claude Code through Agent Client Protocol via a local bridge",
    api_mode="chat_completions",
    env_vars=(),
    base_url=base_url,
    auth_type="api_key",
    supports_health_check=False,
    fallback_models=FALLBACK_MODELS,
    default_max_tokens=max_output_tokens_for(DEFAULT_MODEL),
    default_aux_model="claude-haiku-4-5",
)

register_provider(claude_acp)
