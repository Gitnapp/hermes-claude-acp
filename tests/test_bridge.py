from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from hermes_claude_acp.bridge import (
    build_acpx_command,
    extract_tool_calls,
    format_messages_as_prompt,
    run_acpx_prompt,
)
from hermes_claude_acp.models import FALLBACK_MODELS, max_output_tokens_for, model_cards
from hermes_claude_acp.install_config import PROVIDER_KEY, build_provider_config, merge_config


class BridgeTests(unittest.TestCase):
    def test_format_messages_as_prompt_includes_transcript_and_tools(self):
        prompt = format_messages_as_prompt(
            [
                {"role": "system", "content": "Be exact."},
                {"role": "user", "content": "Say hi"},
            ],
            model="claude-sonnet-4-6",
            tools=[
                {
                    "type": "function",
                    "function": {
                        "name": "lookup",
                        "description": "Look up a thing",
                        "parameters": {"type": "object"},
                    },
                }
            ],
        )

        self.assertIn("Hermes requested model hint: claude-sonnet-4-6", prompt)
        self.assertIn("SYSTEM:\nBe exact.", prompt)
        self.assertIn("USER:\nSay hi", prompt)
        self.assertIn('"name": "lookup"', prompt)

    def test_build_acpx_command_uses_claude_exec_stdin(self):
        command = build_acpx_command(
            model="claude-sonnet-4-6",
            cwd="/tmp",
            timeout_seconds=12,
            permission_mode="deny-all",
        )

        self.assertEqual(command[-4:], ["claude", "exec", "--file", "-"])
        self.assertIn("--model", command)
        self.assertIn("claude-sonnet-4-6", command)
        self.assertIn("--deny-all", command)
        self.assertIn("--timeout", command)

    def test_extract_tool_calls_removes_blocks(self):
        text = (
            "I will call it.\n"
            '<tool_call>{"id":"call_1","type":"function","function":{"name":"lookup","arguments":{"q":"x"}}}</tool_call>'
            "\nDone."
        )

        calls, cleaned = extract_tool_calls(text)

        self.assertEqual(cleaned, "I will call it.\nDone.")
        self.assertEqual(
            calls,
            [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "lookup", "arguments": '{"q": "x"}'},
                }
            ],
        )

    def test_run_acpx_prompt_returns_stdout(self):
        completed = type(
            "Completed",
            (),
            {"returncode": 0, "stdout": "hello\n", "stderr": ""},
        )()
        with patch("hermes_claude_acp.bridge.subprocess.run", return_value=completed) as run:
            result = run_acpx_prompt(
                "prompt",
                model="claude-sonnet-4-6",
                cwd="/tmp",
                timeout_seconds=3,
            )

        self.assertEqual(result, "hello")
        kwargs = run.call_args.kwargs
        self.assertEqual(kwargs["input"], "prompt")
        self.assertEqual(kwargs["timeout"], 18)

    def test_run_acpx_prompt_raises_on_failure(self):
        completed = type(
            "Completed",
            (),
            {"returncode": 1, "stdout": "", "stderr": "boom"},
        )()
        with patch("hermes_claude_acp.bridge.subprocess.run", return_value=completed):
            with self.assertRaisesRegex(RuntimeError, "boom"):
                run_acpx_prompt("prompt", model=None, cwd="/tmp", timeout_seconds=3)

    def test_model_catalog_exposes_current_claude_metadata(self):
        self.assertEqual(FALLBACK_MODELS[0], "claude-fable-5")
        cards = {card["id"]: card for card in model_cards()}

        self.assertEqual(cards["claude-fable-5"]["context_length"], 1_000_000)
        self.assertEqual(cards["claude-fable-5"]["max_output_tokens"], 128_000)
        self.assertEqual(cards["claude-opus-4-8"]["context_length"], 1_000_000)
        self.assertEqual(cards["claude-sonnet-4-6"]["max_completion_tokens"], 128_000)
        self.assertEqual(cards["claude-haiku-4-5"]["context_length"], 200_000)
        self.assertEqual(max_output_tokens_for("claude-haiku-4-5-20251001"), 64_000)

    def test_install_config_merges_provider_without_overwriting_default(self):
        config = {
            "model": {
                "provider": "claude-max",
                "base_url": "http://localhost:3456/v1",
                "default": "claude-opus-4-8",
            },
            "providers": {
                "claude-max": {"base_url": "http://localhost:3456/v1"},
            },
        }

        changed = merge_config(config)

        self.assertTrue(changed)
        self.assertEqual(config["model"]["provider"], "claude-max")
        self.assertIn(PROVIDER_KEY, config["providers"])
        self.assertEqual(
            config["providers"][PROVIDER_KEY]["models"]["claude-fable-5"]["context_length"],
            1_000_000,
        )

    def test_install_config_can_set_default(self):
        config = {}

        merge_config(config, set_default=True)

        self.assertEqual(config["model"]["provider"], PROVIDER_KEY)
        self.assertEqual(config["model"]["default"], "claude-fable-5")
        self.assertEqual(build_provider_config()["default_model"], "claude-fable-5")


if __name__ == "__main__":
    unittest.main()
