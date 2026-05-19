/**
 * Vercel AI SDK middleware — wrap any `LanguageModelV2` so every generation
 * is captured as a Step on the active Loupe trace.
 *
 * @example
 *   import { wrapModel } from "@loupe/sdk/ai-sdk";
 *   import { anthropic } from "@ai-sdk/anthropic";
 *
 *   const model = wrapModel(anthropic("claude-sonnet-4-6"));
 *   const myAgent = trace({ framework: "ai-sdk" }, async (q) => {
 *     return await generateText({ model, prompt: q });
 *   });
 *
 * The implementation is intentionally minimal: we wrap `doGenerate` /
 * `doStream` and record the result. Token usage (when reported by the model)
 * is captured. If the Vercel AI SDK's middleware API is preferred, you can
 * also pass `loupeMiddleware()` into `wrapLanguageModel({ model, middleware })`.
 */

import { redact } from "../_redact.js";
import { closeStep, currentTrace, openStep } from "../trace.js";
import type { Step } from "../types.js";
import { withSuppressedHttpCapture } from "./index.js";

// Vercel AI SDK's actual LanguageModelV2 type lives in the optional `ai` peer
// dependency. We accept anything shaped like it — methods take any params and
// return any value, since we only proxy.
// eslint-disable-next-line @typescript-eslint/no-explicit-any
type AnyFn = (...args: any[]) => any;

interface AnyModel {
  modelId?: string;
  provider?: string;
  doGenerate?: AnyFn;
  doStream?: AnyFn;
  [key: string]: unknown;
}

/**
 * Returns a proxy of `model` that records each call. The original model is
 * preserved — this only adds telemetry; behavior is unchanged.
 */
export function wrapModel<M extends AnyModel>(model: M): M {
  const handler: ProxyHandler<M> = {
    get(target, prop, receiver) {
      const value = Reflect.get(target, prop, receiver);

      if (prop === "doGenerate" && typeof value === "function") {
        return async function loupeDoGenerate(this: unknown, ...args: unknown[]) {
          const step = _openLlmStep(target, args[0]);
          try {
            // Suppress universal fetch capture while the SDK runs — otherwise
            // patchFetch would emit a second Step for the same call.
            const result = await withSuppressedHttpCapture(() => value.apply(target, args));
            if (step) _closeWithResult(step, result);
            return result;
          } catch (err) {
            if (step) closeStep(step, { error: formatError(err) });
            throw err;
          }
        };
      }

      if (prop === "doStream" && typeof value === "function") {
        return async function loupeDoStream(this: unknown, ...args: unknown[]) {
          const step = _openLlmStep(target, args[0], { streaming: true });
          try {
            const result = await withSuppressedHttpCapture(() => value.apply(target, args));
            // Streams are captured at "started" time; full output aggregation
            // can be layered on later by inspecting the returned stream.
            if (step) closeStep(step, { outputs: { stream: "started" } });
            return result;
          } catch (err) {
            if (step) closeStep(step, { error: formatError(err) });
            throw err;
          }
        };
      }

      return value;
    },
  };
  return new Proxy(model, handler);
}

/**
 * Vercel AI SDK middleware-style export. Use with `wrapLanguageModel`.
 *
 *     wrapLanguageModel({ model: anthropic("..."), middleware: loupeMiddleware() })
 */
export function loupeMiddleware() {
  return {
    wrapGenerate: async ({
      doGenerate,
      params,
    }: {
      doGenerate: () => Promise<unknown>;
      params: unknown;
      model?: AnyModel;
    }) => {
      const step = _openLlmStep(undefined, params);
      try {
        const result = await doGenerate();
        if (step) _closeWithResult(step, result);
        return result;
      } catch (err) {
        if (step) closeStep(step, { error: formatError(err) });
        throw err;
      }
    },
    wrapStream: async ({
      doStream,
      params,
    }: {
      doStream: () => Promise<unknown>;
      params: unknown;
      model?: AnyModel;
    }) => {
      const step = _openLlmStep(undefined, params, { streaming: true });
      try {
        const result = await doStream();
        if (step) closeStep(step, { outputs: { stream: "started" } });
        return result;
      } catch (err) {
        if (step) closeStep(step, { error: formatError(err) });
        throw err;
      }
    },
  };
}

function _openLlmStep(
  model: AnyModel | undefined,
  params: unknown,
  extras: Record<string, unknown> = {},
): Step | null {
  if (!currentTrace()) return null;
  const modelId =
    (typeof model === "object" && model && typeof model.modelId === "string"
      ? model.modelId
      : (params as { model?: string } | undefined)?.model) ?? "ai-sdk-model";
  const provider =
    typeof model === "object" && model && typeof model.provider === "string"
      ? model.provider
      : undefined;
  return openStep("llm-call", `ai-sdk:${modelId}`, {
    inputs: _summarizeParams(params),
    metadata: { ...extras, provider },
  });
}

function _closeWithResult(step: Step, result: unknown): void {
  const outputs: Record<string, unknown> = {};
  if (result && typeof result === "object") {
    const r = result as Record<string, unknown>;
    if (typeof r.text === "string") outputs.text = truncate(r.text);
    if (r.usage && typeof r.usage === "object") {
      const u = r.usage as Record<string, unknown>;
      outputs.input_tokens = u.inputTokens ?? u.promptTokens ?? null;
      outputs.output_tokens = u.outputTokens ?? u.completionTokens ?? null;
    }
    if (typeof r.finishReason === "string") outputs.finish_reason = r.finishReason;
  }
  closeStep(step, { outputs });
}

function _summarizeParams(params: unknown): Record<string, unknown> {
  if (!params || typeof params !== "object") return {};
  const p = params as Record<string, unknown>;
  return {
    prompt: truncate(redact(p.prompt)),
    messages: truncate(redact(p.messages)),
    temperature: p.temperature ?? null,
    maxTokens: p.maxOutputTokens ?? p.maxTokens ?? null,
  };
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
