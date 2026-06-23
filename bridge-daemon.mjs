#!/usr/bin/env node
/**
 * Hermes Claude ACP bridge daemon — pre-warms Claude SDK sessions so chat
 * completions don't pay subprocess cold-start cost on every request.
 *
 * Architecture:
 *   One warm session slot is kept ready at all times.
 *   Incoming requests grab the warm slot, query, close, then trigger a
 *   background re-warm so the next request hits a ready session.
 */

import { startup } from "@anthropic-ai/claude-agent-sdk";
import http from "node:http";
import { createRequire } from "node:module";

// ── Config ──────────────────────────────────────────────────────────
const HOST = process.env.HERMES_CLAUDE_ACP_HOST || "127.0.0.1";
const PORT = parseInt(process.env.HERMES_CLAUDE_ACP_PORT || "3457", 10);
const WARM_TIMEOUT_MS = 120_000;
const DEBUG = process.env.HERMES_CLAUDE_ACP_DEBUG === "1";
const DEFAULT_MODEL = "claude-sonnet-4-6";
const DEFAULT_EFFORT = "xhigh"; // best for coding/agentic use cases

// ── Model metadata ──────────────────────────────────────────────────
// Source: https://platform.claude.com/docs/en/about-claude/models/overview
const MODEL_CARDS = [
  {
    id: "claude-opus-4-8",
    name: "Claude Opus 4.8",
    description: "Most capable Opus-tier model for complex reasoning and agentic coding",
    context_length: 1_000_000,
    max_tokens: 128_000,
    effort: "xhigh", // Opus 4.8 supports adaptive thinking: low/medium/high/xhigh/max
  },
  {
    id: "claude-sonnet-4-6",
    name: "Claude Sonnet 4.6",
    description: "Best combination of speed and intelligence",
    context_length: 1_000_000,
    max_tokens: 128_000,
    effort: "xhigh", // Sonnet 4.6 supports adaptive + extended thinking
  },
  {
    id: "claude-haiku-4-5",
    name: "Claude Haiku 4.5",
    description: "Fastest model with near-frontier intelligence",
    context_length: 200_000,
    max_tokens: 64_000,
    effort: null, // Haiku 4.5 does not support extended thinking
  },
];

const MODEL_MAP = Object.fromEntries(MODEL_CARDS.map((m) => [m.id, m]));

function effortFor(model) {
  return MODEL_MAP[model]?.effort ?? DEFAULT_EFFORT;
}

// ── Warm-session slot ──────────────────────────────────────────────
let warmSlot = null; // { query, close, model } | null
let warming = false;
let warmPromise = null;

async function preWarm(model) {
  if (warming) return warmPromise;
  warming = true;

  warmPromise = (async () => {
    const startedAt = Date.now();
    const opts = {
      model: model || DEFAULT_MODEL,
      cwd: process.env.HERMES_CLAUDE_ACP_CWD || process.cwd(),
      permissionMode: "bypassPermissions",
      maxTurns: 1,
      includePartialMessages: false,
      persistSession: false,
      effort: effortFor(model || DEFAULT_MODEL),
    };
    console.error(
      `[warm] starting warm session (model=${opts.model}, cwd=${opts.cwd})`,
    );

    const instance = await startup({
      options: opts,
      initializeTimeoutMs: WARM_TIMEOUT_MS,
    });

    const elapsed = Date.now() - startedAt;
    console.error(`[warm] session ready in ${elapsed}ms`);

    warmSlot = {
      query: instance.query,
      close: instance.close,
      model: opts.model,
    };
    warming = false;
    return warmSlot;
  })().catch((err) => {
    console.error(`[warm] startup failed: ${err.message}`);
    warming = false;
    warmSlot = null;
    warmPromise = null;
    throw err;
  });

  return warmPromise;
}

// ── Prompt formatting ───────────────────────────────────────────────
const TOOL_CALL_BLOCK_RE = /<tool_call>\s*(\{.*?\})\s*<\/tool_call>/gs;
const TOOL_CALL_JSON_RE =
  /\{\s*"id"\s*:\s*"[^"]+"\s*,\s*"type"\s*:\s*"function"\s*,\s*"function"\s*:\s*\{.*?\}\s*\}/gs;

function formatPrompt(messages, model, tools, toolChoice) {
  const sections = [
    "You are the Claude ACP backend for Hermes.",
    "Complete the latest user request using the ACP Claude agent.",
    'If Hermes tools are needed, output tool calls as <tool_call>{"id":"...","type":"function","function":{"name":"...","arguments":"..."}}</tool_call> blocks.',
  ];

  if (model) sections.push(`Hermes requested model hint: ${model}`);

  const toolSpecs = normalizeTools(tools);
  if (toolSpecs.length)
    sections.push(
      "Hermes tool schemas:\n" + JSON.stringify(toolSpecs, null, 2),
    );
  if (toolChoice !== undefined && toolChoice !== null)
    sections.push("Hermes tool_choice:\n" + JSON.stringify(toolChoice));

  const transcript = [];
  for (const msg of messages) {
    if (!msg || typeof msg !== "object") continue;
    const role = (msg.role || "context").toLowerCase();
    let content = renderContent(msg.content);
    if (!content && msg.tool_calls)
      content = "tool_calls:\n" + JSON.stringify(msg.tool_calls);
    if (!content) continue;
    transcript.push(`${role.toUpperCase()}:\n${content}`);
  }

  if (transcript.length)
    sections.push("Conversation transcript:\n\n" + transcript.join("\n\n"));
  sections.push("Continue from the latest user message.");
  return sections.filter(Boolean).join("\n\n");
}

function normalizeTools(tools) {
  if (!Array.isArray(tools)) return [];
  return tools
    .map((t) => t?.function)
    .filter((fn) => fn && typeof fn.name === "string" && fn.name.trim())
    .map((fn) => ({
      name: fn.name.trim(),
      description: fn.description || "",
      parameters: fn.parameters || {},
    }));
}

function renderContent(content) {
  if (content == null) return "";
  if (typeof content === "string") return content.trim();
  if (typeof content === "object" && !Array.isArray(content)) {
    if (typeof content.text === "string") return content.text.trim();
    return JSON.stringify(content);
  }
  if (Array.isArray(content)) {
    const parts = [];
    for (const item of content) {
      if (typeof item === "string") parts.push(item);
      else if (item && typeof item === "object") {
        if (typeof item.text === "string") parts.push(item.text.trim());
        else if (item.type === "image_url")
          parts.push("[image omitted by claude-acp bridge]");
        else parts.push(JSON.stringify(item));
      }
    }
    return parts.filter(Boolean).join("\n");
  }
  return String(content).trim();
}

function extractToolCalls(text) {
  if (!text) return { calls: [], cleaned: "" };
  const calls = [];
  const spans = [];

  for (const m of text.matchAll(TOOL_CALL_BLOCK_RE)) {
    try {
      const obj = JSON.parse(m[1]);
      if (obj?.function?.name) {
        calls.push({
          id: obj.id || `call_claude_acp_${calls.length + 1}`,
          type: "function",
          function: {
            name: obj.function.name,
            arguments:
              typeof obj.function.arguments === "string"
                ? obj.function.arguments
                : JSON.stringify(obj.function.arguments || {}),
          },
        });
        spans.push([m.index, m.index + m[0].length]);
      }
    } catch {}
  }

  if (!calls.length) {
    for (const m of text.matchAll(TOOL_CALL_JSON_RE)) {
      try {
        const obj = JSON.parse(m[0]);
        if (obj?.function?.name) {
          calls.push({
            id: obj.id || `call_claude_acp_${calls.length + 1}`,
            type: "function",
            function: {
              name: obj.function.name,
              arguments:
                typeof obj.function.arguments === "string"
                  ? obj.function.arguments
                  : JSON.stringify(obj.function.arguments || {}),
            },
          });
          spans.push([m.index, m.index + m[0].length]);
        }
      } catch {}
    }
  }

  if (!spans.length) return { calls, cleaned: text.trim() };

  // Remove tool call blocks, keep rest
  const chunks = [];
  let cursor = 0;
  for (const [start, end] of spans.sort((a, b) => a[0] - b[0])) {
    if (cursor < start) chunks.push(text.slice(cursor, start));
    cursor = Math.max(cursor, end);
  }
  if (cursor < text.length) chunks.push(text.slice(cursor));
  return { calls, cleaned: chunks.map((c) => c.trim()).filter(Boolean).join("\n") };
}

// ── JSON helpers ────────────────────────────────────────────────────
function responseId() {
  return `chatcmpl-claude-acp-${Date.now()}`;
}

function jsonResponse(res, status, body) {
  const payload = JSON.stringify(body);
  res.writeHead(status, {
    "Content-Type": "application/json",
    "Content-Length": Buffer.byteLength(payload),
  });
  res.end(payload);
}

function parseBody(req) {
  return new Promise((resolve, reject) => {
    const chunks = [];
    req.on("data", (c) => chunks.push(c));
    req.on("end", () => {
      try {
        resolve(JSON.parse(Buffer.concat(chunks).toString("utf-8")));
      } catch (e) {
        reject(e);
      }
    });
    req.on("error", reject);
  });
}

// ── Route handlers ──────────────────────────────────────────────────
async function handleModels(req, res) {
  jsonResponse(res, 200, {
    object: "list",
    data: MODEL_CARDS.map((m) => ({
      id: m.id,
      object: "model",
      owned_by: "claude-acp",
      name: m.name,
      description: "",
      context_length: m.context_length,
      context_window: m.context_length,
      max_input_tokens: m.context_length,
      max_tokens: m.max_tokens,
      max_output_tokens: m.max_tokens,
      max_completion_tokens: m.max_tokens,
      aliases: [],
    })),
  });
}

async function handleChatCompletions(req, res) {
  let body;
  try {
    body = await parseBody(req);
  } catch {
    return jsonResponse(res, 400, { error: { message: "Invalid JSON body" } });
  }

  const messages = body.messages;
  if (!Array.isArray(messages)) {
    return jsonResponse(res, 400, { error: { message: "messages must be an array" } });
  }

  const model = String(body.model || DEFAULT_MODEL);
  const prompt = formatPrompt(messages, model, body.tools, body.tool_choice);

  // Grab the warm slot if its model matches, otherwise skip
  let instance;
  if (warmSlot && warmSlot.model === model) {
    instance = warmSlot;
    warmSlot = null;
  }

  // Start background re-warm immediately (even if we're falling back to one-shot)
  if (!warming) preWarm(model).catch(() => {});

  try {
    let responseText;

    if (instance) {
      // Use pre-warmed session
      console.error(`[req] using warm session (model=${model})`);
      const startedAt = Date.now();
      const qi = instance.query(prompt);
      // Collect full response — qi is async iterable
      const chunks = [];
      for await (const event of qi) {
        if (!event) continue;
        // Debug: log event type to understand the SDK output format
        if (DEBUG) {
          console.error(`[debug] event type=${event.type} subtype=${event.subtype} keys=${Object.keys(event).join(",")}`);
        }
        if (event.type === "result") {
          if (event.subtype === "error_during_execution" || event.errors?.length) {
            const errMsg = event.errors?.[0]?.message || event.result || "unknown error";
            throw new Error(errMsg);
          }
          responseText = event.result || event.text || event.data || "";
        } else if (event.type === "assistant" && event.message?.content) {
          for (const block of event.message.content) {
            if (block.type === "text") chunks.push(block.text);
          }
        } else if (event.type === "stream_event" && event.event?.type === "content_block_delta") {
          const delta = event.event?.delta;
          if (delta?.type === "text_delta") chunks.push(delta.text);
        }
      }
      if (!responseText && chunks.length) responseText = chunks.join("");
      const elapsed = Date.now() - startedAt;
      console.error(`[req] warm query done in ${elapsed}ms`);
      try { await instance.close?.(); } catch {}
      try { await qi?.close?.(); } catch {}
    } else {
      // Fallback: one-shot query (pays cold start)
      console.error(`[req] no warm slot — falling back to one-shot query (model=${model})`);
      const startedAt = Date.now();
      const qi = await startup({
        options: {
          model,
          cwd: process.env.HERMES_CLAUDE_ACP_CWD || process.cwd(),
          permissionMode: "bypassPermissions",
          maxTurns: 1,
          persistSession: false,
          effort: effortFor(model),
        },
        initializeTimeoutMs: WARM_TIMEOUT_MS,
      });
      const qi2 = qi.query(prompt);
      const chunks = [];
      for await (const event of qi2) {
        if (event.type === "result") {
          if (event.subtype === "error_during_execution" || event.errors?.length) {
            const errMsg = event.errors?.[0]?.message || event.result || "unknown error";
            throw new Error(errMsg);
          }
          responseText = event.result || event.text || event.data || "";
        } else if (event.type === "assistant" && event.message?.content) {
          for (const block of event.message.content) {
            if (block.type === "text") chunks.push(block.text);
          }
        } else if (event.type === "stream_event" && event.event?.type === "content_block_delta") {
          const delta = event.event?.delta;
          if (delta?.type === "text_delta") chunks.push(delta.text);
        }
      }
      if (!responseText && chunks.length) responseText = chunks.join("");
      const elapsed = Date.now() - startedAt;
      console.error(`[req] one-shot query done in ${elapsed}ms`);
      try { await qi.close?.(); } catch {}
      try { await qi2?.close?.(); } catch {}
    }

    responseText = responseText || "";

    if (body.stream === true) {
      // SSE stream mode
      const rid = responseId();
      const created = Math.floor(Date.now() / 1000);
      res.writeHead(200, {
        "Content-Type": "text/event-stream",
        "Cache-Control": "no-cache",
        Connection: "close",
      });
      res.write(
        `data: ${JSON.stringify({ id: rid, object: "chat.completion.chunk", created, model, choices: [{ index: 0, delta: { role: "assistant" }, finish_reason: null }] })}\n\n`,
      );
      if (responseText) {
        res.write(
          `data: ${JSON.stringify({ id: rid, object: "chat.completion.chunk", created, model, choices: [{ index: 0, delta: { content: responseText }, finish_reason: null }] })}\n\n`,
        );
      }
      res.write(
        `data: ${JSON.stringify({ id: rid, object: "chat.completion.chunk", created, model, choices: [{ index: 0, delta: {}, finish_reason: "stop" }] })}\n\n`,
      );
      res.write("data: [DONE]\n\n");
      res.end();
    } else {
      // Non-streaming
      const { calls: toolCalls, cleaned } = extractToolCalls(responseText);
      const message = { role: "assistant", content: cleaned };
      let finishReason = "stop";
      if (toolCalls.length) {
        message.tool_calls = toolCalls;
        finishReason = "tool_calls";
      }
      jsonResponse(res, 200, {
        id: responseId(),
        object: "chat.completion",
        created: Math.floor(Date.now() / 1000),
        model,
        choices: [{ index: 0, message, finish_reason: finishReason }],
        usage: { prompt_tokens: 0, completion_tokens: 0, total_tokens: 0 },
      });
    }
  } catch (err) {
    console.error(`[err] ${err.message}`);
    jsonResponse(res, 500, {
      error: { message: err.message, type: err.constructor.name },
    });
  }
}

// ── Main ────────────────────────────────────────────────────────────
const server = http.createServer(async (req, res) => {
  try {
    if (req.method === "GET" && req.url.replace(/\/$/, "") === "/v1/models") {
      return await handleModels(req, res);
    }
    if (req.method === "POST" && req.url.replace(/\/$/, "") === "/v1/chat/completions") {
      return await handleChatCompletions(req, res);
    }
    jsonResponse(res, 404, { error: "not found" });
  } catch (err) {
    console.error(`[fatal] ${err.stack || err.message}`);
    if (!res.headersSent) {
      jsonResponse(res, 500, { error: { message: "internal server error" } });
    }
  }
});

// Pre-warm the first session before accepting requests
console.error(`[init] pre-warming first session...`);
preWarm(DEFAULT_MODEL)
  .then(() => {
    server.listen(PORT, HOST, () => {
      console.error(`[init] bridge listening on http://${HOST}:${PORT}/v1`);
      // stdout line for launchd / log compatibility
      console.log(
        `Hermes Claude ACP bridge (SDK daemon) listening on http://${HOST}:${PORT}/v1`,
      );
    });
  })
  .catch((err) => {
    console.error(`[fatal] initial warm-up failed: ${err.message}`);
    // Start server anyway — requests will fall back to one-shot
    server.listen(PORT, HOST, () => {
      console.error(
        `[init] bridge listening (degraded — no warm slot) on http://${HOST}:${PORT}/v1`,
      );
      console.log(
        `Hermes Claude ACP bridge (SDK daemon, degraded) listening on http://${HOST}:${PORT}/v1`,
      );
    });
  });

// Graceful shutdown
process.on("SIGTERM", () => {
  console.error("[shutdown] closing...");
  try { warmSlot?.close?.(); } catch {}
  server.close(() => process.exit(0));
});
