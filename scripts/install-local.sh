#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
hermes_home="${HERMES_HOME:-$HOME/.hermes}"

mkdir -p "$hermes_home/plugins/model-providers"

plugin_link="$hermes_home/plugins/hermes-claude-acp"
provider_link="$hermes_home/plugins/model-providers/claude-acp"

ln -sfn "$repo_root" "$plugin_link"
ln -sfn "$repo_root/model-providers/claude-acp" "$provider_link"

echo "Linked plugin: $plugin_link -> $repo_root"
echo "Linked provider: $provider_link -> $repo_root/model-providers/claude-acp"
echo

if [[ ! -x "$repo_root/node_modules/.bin/acpx" ]]; then
  echo "Installing ACP runtime dependencies..."
  (cd "$repo_root" && npm install)
fi

if command -v hermes >/dev/null 2>&1; then
  hermes plugins enable hermes-claude-acp
  hermes plugins enable claude-acp-provider
else
  echo "hermes command not found; enable plugins after Hermes is available."
fi

hermes_python="$hermes_home/hermes-agent/venv/bin/python"
if [[ -x "$hermes_python" ]]; then
  config_args=()
  if [[ "${HERMES_CLAUDE_ACP_SET_DEFAULT:-0}" == "1" ]]; then
    config_args+=(--set-default)
  fi
  if [[ ${#config_args[@]} -gt 0 ]]; then
    PYTHONPATH="$repo_root:$hermes_home/hermes-agent${PYTHONPATH:+:$PYTHONPATH}" \
      "$hermes_python" -m hermes_claude_acp.install_config "${config_args[@]}"
  else
    PYTHONPATH="$repo_root:$hermes_home/hermes-agent${PYTHONPATH:+:$PYTHONPATH}" \
      "$hermes_python" -m hermes_claude_acp.install_config
  fi
else
  echo "Hermes Python venv not found; skipped config merge for providers.claude-acp."
fi

echo
echo "Provider registered as: claude-acp"
echo "Default bridge URL: http://127.0.0.1:3457/v1"
echo
echo "Run the bridge:"
echo "  cd '$repo_root' && python3 -m hermes_claude_acp.bridge --host 127.0.0.1 --port 3457"
