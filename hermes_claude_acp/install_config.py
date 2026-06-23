"""Install-time Hermes config merge for the Claude ACP provider."""

from __future__ import annotations

import argparse
from typing import Any

from .models import CLAUDE_MODELS, DEFAULT_MODEL
from .provider import DEFAULT_BASE_URL


PROVIDER_KEY = "claude-acp"


def build_provider_config() -> dict[str, Any]:
    models: dict[str, dict[str, int]] = {}
    for spec in CLAUDE_MODELS:
        entry = {
            "context_length": spec.context_length,
            "max_output_tokens": spec.max_output_tokens,
        }
        models[spec.id] = dict(entry)
        for alias in spec.aliases:
            models[alias] = dict(entry)

    return {
        "name": "Claude ACP",
        "base_url": DEFAULT_BASE_URL,
        "api_mode": "chat_completions",
        "key_env": "",
        "default_model": DEFAULT_MODEL,
        "discover_models": False,
        "models": models,
    }


def merge_config(config: dict[str, Any], *, set_default: bool = False) -> bool:
    changed = False

    providers = config.get("providers")
    if not isinstance(providers, dict):
        providers = {}
        config["providers"] = providers
        changed = True

    desired = build_provider_config()
    current = providers.get(PROVIDER_KEY)
    if not isinstance(current, dict):
        providers[PROVIDER_KEY] = desired
        changed = True
    else:
        for key, value in desired.items():
            if current.get(key) != value:
                current[key] = value
                changed = True

    if set_default:
        model = config.get("model")
        if not isinstance(model, dict):
            model = {}
            config["model"] = model
            changed = True
        desired_model = {
            "provider": PROVIDER_KEY,
            "base_url": DEFAULT_BASE_URL,
            "default": DEFAULT_MODEL,
            "context_length": 1_000_000,
        }
        for key, value in desired_model.items():
            if model.get(key) != value:
                model[key] = value
                changed = True

    return changed


def install_config(*, set_default: bool = False) -> bool:
    from hermes_cli.config import load_config, save_config

    config = load_config()
    changed = merge_config(config, set_default=set_default)
    if changed:
        save_config(config)
    return changed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Merge Claude ACP into Hermes config")
    parser.add_argument("--set-default", action="store_true")
    args = parser.parse_args(argv)

    changed = install_config(set_default=args.set_default)
    print("Updated Hermes config for claude-acp." if changed else "Hermes config already has claude-acp.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
