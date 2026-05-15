/**
 * Universal fetch capture — works with ANY TypeScript/JavaScript LLM client.
 *
 * Most modern JS/TS LLM clients (the official anthropic, openai, mistralai,
 * @google/generative-ai, groq-sdk, together-ai SDKs, plus the Vercel AI SDK)
 * route their HTTP through the global `fetch`. Patching `globalThis.fetch`
 * once captures all of them.
 *
 * @example
 *   import { patchFetch } from "@loupe/sdk/universal";
 *   patchFetch();
 *
 *   import { trace } from "@loupe/sdk";
 *   const myAgent = trace({ framework: "universal" }, async (q: string) => {
 *     // Use any LLM SDK that calls fetch — captured automatically.
 *     return await someClient.chat({ prompt: q });
 *   });
 */

import { redact } from "../_redact.js";
import { closeStep, currentTrace, openStep } from "../trace.js";
import type { Step } from "../types.js";
import { detectProviderFromHost, looksLikeOpenAICompatible } from "./_providers.js";

const PATCH_FLAG = "__loupePatched__";

type Fetchable = typeof fetch;

/**
 * Monkey-patch a `fetch`-like function so any call to a known LLM provider
 * becomes a Loupe Step. Idempotent. Returns the wrapped fetch in case you
 * want to install it somewhere other than globalThis.
 */
export function patchFetch(globalScope?: { fetch?: Fetchable }): boolean {
  const target = globalScope ?? (globalThis as { fetch?: Fetchable });
  if (typeof target.fetch !== "function") return false;
  // Check the flag on the live fetch reference — .bind() below would create a
  // new function without our property so we can't check after binding.
  if ((target.fetch as Fetchable & { [PATCH_FLAG]?: boolean })[PATCH_FLAG]) return false;

  const original = target.fetch.bind(target);
  const wrapped = makeWrappedFetch(original);
  (wrapped as Fetchable & { [PATCH_FLAG]?: boolean })[PATCH_FLAG] = true;
  target.fetch = wrapped;
  return true;
}

/**
 * Return a wrapped fetch without touching globalThis — useful for tests or
 * for libraries that take a custom `fetch` (most modern SDKs accept one).
 */
export function wrapFetch(original: Fetchable): Fetchable {
  const wrapped = makeWrappedFetch(original);
  (wrapped as Fetchable & { [PATCH_FLAG]?: boolean })[PATCH_FLAG] = true;
  return wrapped;
}

function makeWrappedFetch(original: Fetchable): Fetchable {
  return async function loupeFetch(
    input: RequestInfo | URL,
    init?: RequestInit,
  ): Promise<Response> {
    const url = extractUrl(input);
    if (!currentTrace()) return original(input as RequestInfo, init);

    const body = parseBody(init?.body);
    const provider = classify(url, body);
    if (!provider) return original(input as RequestInfo, init);

    const model = (body && typeof body === "object" && "model" in body)
      ? (body as Record<string, unknown>).model
      : undefined;

    const step = openStep("llm-call", `${provider}:${String(model ?? "unknown")}`, {
      inputs: summarizeInputs(provider, body, init?.method),
      metadata: { transport: "fetch", url: stripUrl(url) },
    });

    try {
      const response = await original(input as RequestInfo, init);
      if (step) {
        // Clone for inspection so the caller still gets a fully readable body.
        await captureResponse(step, provider, response.clone());
        closeStep(step, {
          outputs: { ...step.outputs, status: response.status },
        });
      }
      return response;
    } catch (err) {
      if (step) closeStep(step, { error: formatError(err) });
      throw err;
    }
  };
}

function extractUrl(input: RequestInfo | URL): string {
  if (typeof input === "string") return input;
  if (input instanceof URL) return input.toString();
  return (input as Request).url;
}

function classify(url: string, body: unknown): string | null {
  let host: string | null = null;
  try {
    host = new URL(url).hostname.toLowerCase();
  } catch {
    return null;
  }
  const known = detectProviderFromHost(host);
  if (known) return known.label;
  if (looksLikeOpenAICompatible(body) && host) {
    return `openai-compatible:${host}`;
  }
  return null;
}

function stripUrl(url: string): string {
  try {
    const u = new URL(url);
    return `${u.protocol}//${u.host}${u.pathname}`;
  } catch {
    return url;
  }
}

function parseBody(body: BodyInit | null | undefined): unknown {
  if (body == null) return null;
  if (typeof body === "string") {
    try { return JSON.parse(body); } catch { return null; }
  }
  if (body instanceof ArrayBuffer) {
    try { return JSON.parse(new TextDecoder().decode(body)); } catch { return null; }
  }
  if (ArrayBuffer.isView(body)) {
    try {
      return JSON.parse(new TextDecoder().decode(body as Uint8Array));
    } catch { return null; }
  }
  return null;
}

function summarizeInputs(
  provider: string,
  body: unknown,
  method: string | undefined,
): Record<string, unknown> {
  const out: Record<string, unknown> = { provider, method: method ?? "POST" };
  if (body && typeof body === "object") {
    const b = body as Record<string, unknown>;
    if ("model" in b) out.model = b.model;
    // Run prompts + messages through the redactor before they hit disk.
    if ("messages" in b) out.messages = truncate(redact(b.messages));
    if ("prompt" in b) out.prompt = truncate(redact(b.prompt));
    if ("max_tokens" in b) out.max_tokens = b.max_tokens;
    if ("maxOutputTokens" in b) out.max_tokens = b.maxOutputTokens;
    if (b.stream) out.stream = true;
  }
  return out;
}

async function captureResponse(step: Step, provider: string, response: Response): Promise<void> {
  if (response.headers.get("content-type")?.startsWith("text/event-stream")) {
    step.outputs = { ...step.outputs, streamed: true };
    return;
  }
  try {
    const data = (await response.json()) as Record<string, unknown>;
    Object.assign(step.outputs, summarizeResponse(provider, data));
  } catch {
    // non-JSON; nothing to extract
  }
}

function summarizeResponse(_provider: string, body: Record<string, unknown>): Record<string, unknown> {
  const out: Record<string, unknown> = {};

  // OpenAI-style choices
  const choices = body.choices as Array<Record<string, unknown>> | undefined;
  if (Array.isArray(choices) && choices.length > 0) {
    const msg = choices[0]?.message as Record<string, unknown> | undefined;
    if (msg && typeof msg.content === "string") {
      out.text = truncate(msg.content);
    }
    if (typeof choices[0]?.finish_reason === "string") {
      out.finish_reason = choices[0].finish_reason;
    }
  }

  // Anthropic-style content
  const content = body.content as Array<Record<string, unknown>> | undefined;
  if (Array.isArray(content) && content.length > 0) {
    const block = content[0];
    if (block && typeof block.text === "string") {
      out.text = truncate(block.text);
    }
  }
  if (typeof body.stop_reason === "string") out.stop_reason = body.stop_reason;

  // Gemini-style candidates
  const candidates = body.candidates as Array<Record<string, unknown>> | undefined;
  if (Array.isArray(candidates) && candidates.length > 0) {
    const cnt = candidates[0]?.content as Record<string, unknown> | undefined;
    const parts = cnt?.parts as Array<Record<string, unknown>> | undefined;
    if (Array.isArray(parts) && parts.length > 0) {
      const first = parts[0];
      if (first && typeof first.text === "string") out.text = truncate(first.text);
    }
  }

  // Usage
  const usage = body.usage as Record<string, unknown> | undefined;
  if (usage && typeof usage === "object") {
    out.input_tokens = usage.input_tokens ?? usage.prompt_tokens ?? null;
    out.output_tokens = usage.output_tokens ?? usage.completion_tokens ?? null;
  }

  return out;
}

function truncate(value: unknown, limit = 4000): unknown {
  if (value == null) return value;
  if (typeof value === "string") {
    return value.length > limit ? value.slice(0, limit) + "…[truncated]" : value;
  }
  if (typeof value === "number" || typeof value === "boolean") return value;
  try {
    const s = JSON.stringify(value);
    return s.length > limit ? s.slice(0, limit) + "…[truncated]" : value;
  } catch {
    return String(value).slice(0, limit);
  }
}

function formatError(err: unknown): string {
  if (err instanceof Error) return `${err.constructor.name}: ${err.message}`;
  return String(err);
}
