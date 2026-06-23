"""OpenAI-compatible HTTP bridge from Hermes to Claude ACP through acpx."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from .models import DEFAULT_MODEL, FALLBACK_MODELS, model_cards
from .provider import DEFAULT_HOST, DEFAULT_PORT

DEFAULT_TIMEOUT_SECONDS = float(os.getenv("HERMES_CLAUDE_ACP_TIMEOUT", "900"))
DEFAULT_CWD = os.getenv("HERMES_CLAUDE_ACP_CWD", os.getcwd())
DEFAULT_PERMISSION_MODE = os.getenv("HERMES_CLAUDE_ACP_PERMISSION_MODE", "approve-reads")
DEFAULT_NON_INTERACTIVE = os.getenv("HERMES_CLAUDE_ACP_NON_INTERACTIVE", "deny")
DEFAULT_AUTH_POLICY = os.getenv("HERMES_CLAUDE_ACP_AUTH_POLICY", "skip")

_TOOL_CALL_BLOCK_RE = re.compile(r"<tool_call>\s*(\{.*?\})\s*</tool_call>", re.DOTALL)
_TOOL_CALL_JSON_RE = re.compile(
    r"\{\s*\"id\"\s*:\s*\"[^\"]+\"\s*,\s*\"type\"\s*:\s*\"function\"\s*,\s*\"function\"\s*:\s*\{.*?\}\s*\}",
    re.DOTALL,
)


def resolve_acpx_command() -> str:
    """Find the acpx executable using explicit env, PATH, or Openclaw's install."""
    explicit = os.getenv("HERMES_CLAUDE_ACP_ACPX", "").strip()
    if explicit:
        return explicit

    local = Path(__file__).resolve().parent.parent / "node_modules" / ".bin" / "acpx"
    if local.exists():
        return str(local)

    on_path = shutil.which("acpx")
    if on_path:
        return on_path

    openclaw = Path.home() / ".openclaw" / "npm" / "node_modules" / ".bin" / "acpx"
    if openclaw.exists():
        return str(openclaw)

    return "acpx"


def format_messages_as_prompt(
    messages: list[dict[str, Any]],
    *,
    model: str | None,
    tools: list[dict[str, Any]] | None = None,
    tool_choice: Any = None,
) -> str:
    """Render Chat Completions messages into a single ACP prompt turn."""
    sections: list[str] = [
        "You are the Claude ACP backend for Hermes.",
        "Complete the latest user request using the ACP Claude agent.",
        (
            "If Hermes tools are needed, output tool calls as "
            "<tool_call>{...}</tool_call> blocks containing OpenAI function-call "
            "JSON with id, type=function, and function{name,arguments}. "
            "If no Hermes tool is needed, answer normally."
        ),
    ]
    if model:
        sections.append(f"Hermes requested model hint: {model}")

    tool_specs = _normalize_tool_specs(tools or [])
    if tool_specs:
        sections.append(
            "Hermes tool schemas available to the host:\n"
            + json.dumps(tool_specs, ensure_ascii=False)
        )
    if tool_choice is not None:
        sections.append("Hermes tool_choice:\n" + json.dumps(tool_choice, ensure_ascii=False))

    transcript: list[str] = []
    for message in messages:
        if not isinstance(message, dict):
            continue
        role = str(message.get("role") or "context").strip().lower()
        content = _render_content(message.get("content"))
        if not content and message.get("tool_calls"):
            content = "tool_calls:\n" + json.dumps(message.get("tool_calls"), ensure_ascii=False)
        if not content:
            continue
        transcript.append(f"{role.upper()}:\n{content}")

    if transcript:
        sections.append("Conversation transcript:\n\n" + "\n\n".join(transcript))
    sections.append("Continue from the latest user message.")
    return "\n\n".join(section.strip() for section in sections if section.strip())


def _normalize_tool_specs(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    specs: list[dict[str, Any]] = []
    for tool in tools:
        if not isinstance(tool, dict):
            continue
        fn = tool.get("function")
        if not isinstance(fn, dict):
            continue
        name = fn.get("name")
        if not isinstance(name, str) or not name.strip():
            continue
        specs.append(
            {
                "name": name.strip(),
                "description": fn.get("description", ""),
                "parameters": fn.get("parameters", {}),
            }
        )
    return specs


def _render_content(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, dict):
        if isinstance(content.get("text"), str):
            return content["text"].strip()
        return json.dumps(content, ensure_ascii=False)
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                if isinstance(item.get("text"), str):
                    parts.append(item["text"].strip())
                elif item.get("type") == "image_url":
                    parts.append("[image omitted by claude-acp bridge]")
                else:
                    parts.append(json.dumps(item, ensure_ascii=False))
        return "\n".join(part for part in parts if part).strip()
    return str(content).strip()


def build_acpx_command(
    *,
    model: str | None,
    cwd: str,
    timeout_seconds: float,
    permission_mode: str = DEFAULT_PERMISSION_MODE,
) -> list[str]:
    command = [
        resolve_acpx_command(),
        "--cwd",
        cwd,
        "--format",
        "quiet",
        "--auth-policy",
        DEFAULT_AUTH_POLICY,
        "--non-interactive-permissions",
        DEFAULT_NON_INTERACTIVE,
        "--timeout",
        str(max(1, int(timeout_seconds))),
    ]

    mode = permission_mode.strip().lower()
    if mode == "approve-all":
        command.append("--approve-all")
    elif mode == "deny-all":
        command.append("--deny-all")
    else:
        command.append("--approve-reads")

    if model:
        command.extend(["--model", model])

    extra = os.getenv("HERMES_CLAUDE_ACP_EXTRA_ARGS", "").strip()
    if extra:
        import shlex

        command.extend(shlex.split(extra))

    command.extend(["claude", "exec", "--file", "-"])
    return command


def run_acpx_prompt(
    prompt: str,
    *,
    model: str | None,
    cwd: str = DEFAULT_CWD,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
) -> str:
    command = build_acpx_command(model=model, cwd=cwd, timeout_seconds=timeout_seconds)
    try:
        completed = subprocess.run(
            command,
            input=prompt,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout_seconds + 15,
            cwd=cwd,
            env=_subprocess_env(),
        )
    except FileNotFoundError as exc:
        raise RuntimeError(
            "acpx was not found. Install plugin npm deps with `npm install`, "
            "or set HERMES_CLAUDE_ACP_ACPX to an acpx executable."
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise TimeoutError(f"Claude ACP timed out after {timeout_seconds:.0f}s") from exc

    if completed.returncode != 0:
        stderr = (completed.stderr or "").strip()
        stdout = (completed.stdout or "").strip()
        detail = stderr or stdout or f"acpx exited with code {completed.returncode}"
        raise RuntimeError(detail)

    return (completed.stdout or "").strip()


def _subprocess_env() -> dict[str, str]:
    env = os.environ.copy()
    package_bin = Path(__file__).resolve().parent.parent / "node_modules" / ".bin"
    if package_bin.exists():
        env["PATH"] = f"{package_bin}{os.pathsep}{env.get('PATH', '')}"
    return env


def extract_tool_calls(text: str) -> tuple[list[dict[str, Any]], str]:
    if not text:
        return [], ""

    calls: list[dict[str, Any]] = []
    spans: list[tuple[int, int]] = []

    def add(raw_json: str) -> None:
        try:
            obj = json.loads(raw_json)
        except Exception:
            return
        if not isinstance(obj, dict):
            return
        fn = obj.get("function")
        if not isinstance(fn, dict) or not isinstance(fn.get("name"), str):
            return
        args = fn.get("arguments", "{}")
        if not isinstance(args, str):
            args = json.dumps(args, ensure_ascii=False)
        call_id = obj.get("id")
        if not isinstance(call_id, str) or not call_id:
            call_id = f"call_claude_acp_{len(calls) + 1}"
        calls.append(
            {
                "id": call_id,
                "type": "function",
                "function": {
                    "name": fn["name"],
                    "arguments": args,
                },
            }
        )

    for match in _TOOL_CALL_BLOCK_RE.finditer(text):
        add(match.group(1))
        spans.append((match.start(), match.end()))

    if not calls:
        for match in _TOOL_CALL_JSON_RE.finditer(text):
            add(match.group(0))
            spans.append((match.start(), match.end()))

    if not spans:
        return calls, text.strip()

    chunks: list[str] = []
    cursor = 0
    for start, end in sorted(spans):
        if cursor < start:
            chunks.append(text[cursor:start])
        cursor = max(cursor, end)
    if cursor < len(text):
        chunks.append(text[cursor:])
    return calls, "\n".join(chunk.strip() for chunk in chunks if chunk.strip()).strip()


class ClaudeACPHandler(BaseHTTPRequestHandler):
    server_version = "HermesClaudeACP/0.1"

    def do_GET(self) -> None:
        if self.path.rstrip("/") == "/v1/models":
            self._send_json(
                {
                    "object": "list",
                    "data": model_cards(),
                }
            )
            return
        self._send_json({"error": "not found"}, status=HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        if self.path.rstrip("/") != "/v1/chat/completions":
            self._send_json({"error": "not found"}, status=HTTPStatus.NOT_FOUND)
            return

        try:
            request = self._read_json_body()
            response_text = self._complete(request)
            if request.get("stream") is True:
                self._send_sse_response(response_text, model=str(request.get("model") or "claude-acp"))
            else:
                self._send_completion_response(response_text, model=str(request.get("model") or "claude-acp"))
        except Exception as exc:
            self._send_json(
                {
                    "error": {
                        "message": str(exc),
                        "type": exc.__class__.__name__,
                    }
                },
                status=HTTPStatus.INTERNAL_SERVER_ERROR,
            )

    def log_message(self, format: str, *args: Any) -> None:
        if os.getenv("HERMES_CLAUDE_ACP_DEBUG"):
            super().log_message(format, *args)

    def _read_json_body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0") or "0")
        raw = self.rfile.read(length)
        payload = json.loads(raw.decode("utf-8") if raw else "{}")
        if not isinstance(payload, dict):
            raise ValueError("Request body must be a JSON object")
        return payload

    def _complete(self, request: dict[str, Any]) -> str:
        messages = request.get("messages")
        if not isinstance(messages, list):
            raise ValueError("messages must be a list")
        model = str(request.get("model") or DEFAULT_MODEL)
        prompt = format_messages_as_prompt(
            messages,
            model=model,
            tools=request.get("tools") if isinstance(request.get("tools"), list) else None,
            tool_choice=request.get("tool_choice"),
        )
        timeout = _request_timeout(request)
        cwd = _request_cwd(request)
        return run_acpx_prompt(prompt, model=model, cwd=cwd, timeout_seconds=timeout)

    def _send_completion_response(self, text: str, *, model: str) -> None:
        tool_calls, cleaned = extract_tool_calls(text)
        message: dict[str, Any] = {"role": "assistant", "content": cleaned}
        finish_reason = "stop"
        if tool_calls:
            message["tool_calls"] = tool_calls
            finish_reason = "tool_calls"
        self._send_json(
            {
                "id": _response_id(),
                "object": "chat.completion",
                "created": int(time.time()),
                "model": model,
                "choices": [
                    {
                        "index": 0,
                        "message": message,
                        "finish_reason": finish_reason,
                    }
                ],
                "usage": {
                    "prompt_tokens": 0,
                    "completion_tokens": 0,
                    "total_tokens": 0,
                },
            }
        )

    def _send_sse_response(self, text: str, *, model: str) -> None:
        response_id = _response_id()
        created = int(time.time())
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()

        def write_event(payload: dict[str, Any] | str) -> None:
            data = payload if isinstance(payload, str) else json.dumps(payload)
            self.wfile.write(f"data: {data}\n\n".encode("utf-8"))
            self.wfile.flush()

        write_event(
            {
                "id": response_id,
                "object": "chat.completion.chunk",
                "created": created,
                "model": model,
                "choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}],
            }
        )
        if text:
            write_event(
                {
                    "id": response_id,
                    "object": "chat.completion.chunk",
                    "created": created,
                    "model": model,
                    "choices": [{"index": 0, "delta": {"content": text}, "finish_reason": None}],
                }
            )
        write_event(
            {
                "id": response_id,
                "object": "chat.completion.chunk",
                "created": created,
                "model": model,
                "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
            }
        )
        write_event("[DONE]")

    def _send_json(self, payload: dict[str, Any], *, status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def _request_timeout(request: dict[str, Any]) -> float:
    timeout = request.get("timeout")
    if isinstance(timeout, (int, float)) and timeout > 0:
        return float(timeout)
    return DEFAULT_TIMEOUT_SECONDS


def _request_cwd(request: dict[str, Any]) -> str:
    metadata = request.get("metadata")
    if isinstance(metadata, dict) and isinstance(metadata.get("cwd"), str):
        return str(Path(metadata["cwd"]).expanduser().resolve())
    return str(Path(DEFAULT_CWD).expanduser().resolve())


def _response_id() -> str:
    return f"chatcmpl-claude-acp-{int(time.time() * 1000)}"


def serve(host: str = DEFAULT_HOST, port: int = DEFAULT_PORT) -> None:
    server = ThreadingHTTPServer((host, port), ClaudeACPHandler)
    print(f"Hermes Claude ACP bridge listening on http://{host}:{port}/v1", flush=True)
    server.serve_forever()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the Hermes Claude ACP bridge")
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--check", action="store_true", help="Print resolved acpx command and exit")
    args = parser.parse_args(argv)

    if args.check:
        print(resolve_acpx_command())
        return 0

    serve(args.host, args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
