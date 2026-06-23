# Hermes Claude ACP

Hermes plugin/bridge for calling Claude Code through ACP, following Openclaw's ACPX pattern:

- Hermes talks OpenAI-compatible Chat Completions to a local bridge.
- The bridge shells out to `acpx claude exec --file -`.
- `acpx` launches `@agentclientprotocol/claude-agent-acp`, the same Claude ACP adapter family used by Openclaw's `@openclaw/acpx`.

## Install

```bash
cd /Users/eric/dev/hermes-claude-acp
npm install
scripts/install-local.sh
```

The install script links and enables both the standalone plugin and the
`claude-acp` model-provider manifest. It also merges `providers.claude-acp`
into `~/.hermes/config.yaml` with the current Claude model metadata. It does
not overwrite your active model provider unless `HERMES_CLAUDE_ACP_SET_DEFAULT=1`
is set.

Run the bridge:

```bash
python -m hermes_claude_acp.bridge --host 127.0.0.1 --port 3457
```

The provider registers as a local OpenAI-compatible provider at
`http://127.0.0.1:3457/v1`. It declares no required API-key environment
variables because auth is handled by Claude Code through ACP. Both the Hermes
provider config and bridge expose the current Claude catalog with context and
output limits:

| Model | Context | Max output |
| --- | ---: | ---: |
| `claude-fable-5` | 1,000,000 | 128,000 |
| `claude-opus-4-8` | 1,000,000 | 128,000 |
| `claude-sonnet-4-6` | 1,000,000 | 128,000 |
| `claude-haiku-4-5` | 200,000 | 64,000 |

Make it the active provider manually:

```bash
hermes config set model.provider claude-acp
hermes config set model.base_url http://127.0.0.1:3457/v1
hermes config set model.default claude-fable-5
```

Or let the install script set the active provider:

```bash
HERMES_CLAUDE_ACP_SET_DEFAULT=1 scripts/install-local.sh
```

## Useful Environment

- `HERMES_CLAUDE_ACP_ACPX`: explicit `acpx` executable.
- `HERMES_CLAUDE_ACP_BASE_URL`: provider base URL override.
- `HERMES_CLAUDE_ACP_CWD`: working directory passed to acpx.
- `HERMES_CLAUDE_ACP_PERMISSION_MODE`: `approve-reads` (default), `approve-all`, or `deny-all`.
- `HERMES_CLAUDE_ACP_NON_INTERACTIVE`: `deny` (default) or `fail`.
- `HERMES_CLAUDE_ACP_TIMEOUT`: bridge/acpx timeout seconds.
- `HERMES_CLAUDE_ACP_EXTRA_ARGS`: extra global acpx flags.

## Local Checks

```bash
python -m unittest discover -s tests
python -m hermes_claude_acp.bridge --check
curl http://127.0.0.1:3457/v1/models
```
