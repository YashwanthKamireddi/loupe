/**
 * Mastra integration — capture every Agent.generate / Agent.stream call.
 *
 * Mastra (https://mastra.ai) is the popular open-source TypeScript agent
 * framework. Its core type is `Agent`, which exposes:
 *   agent.generate(prompt, options?)   // -> GenerateResult
 *   agent.stream(prompt, options?)     // -> StreamResult
 *
 * `patchMastraAgent(AgentClass)` monkey-patches both methods on the prototype
 * so every agent instance is automatically traced. Idempotent.
 *
 * Usage:
 *   import { Agent } from "@mastra/core";
 *   import { patchMastraAgent } from "@loupe/sdk/mastra";
 *   patchMastraAgent(Agent);
 *
 *   const agent = new Agent({ name: "writer", model: ... });
 *   const result = await trace({ framework: "mastra" }, async () => {
 *     return await agent.generate("hi");
 *   })();
 */

import { redact } from "../_redact.js";
import { closeStep, currentTrace, openStep } from "../trace.js";

const PATCH_FLAG = "__loupePatched__";

// Permissive shape — any class with a prototype whose `generate` / `stream`
// methods we'll proxy. Most user agent subclasses have richer types than
// this; we don't care.
type AgentLike = {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  prototype: any;
};

type AgentProto = {
  name?: string;
  generate?: (...args: unknown[]) => Promise<unknown>;
  stream?: (...args: unknown[]) => Promise<unknown>;
};

/**
 * Patch a Mastra `Agent` class (or any class with a matching shape). Idempotent.
 * Returns true if patching happened, false if already patched.
 */
export function patchMastraAgent(AgentClass: AgentLike): boolean {
  const proto = AgentClass.prototype as AgentProto;
  if (!proto) return false;
  let changed = false;

  const gen = proto.generate;
  if (typeof gen === "function" && !(gen as unknown as Record<string, unknown>)[PATCH_FLAG]) {
    proto.generate = wrap(gen, "generate");
    changed = true;
  }
  const stream = proto.stream;
  if (typeof stream === "function" && !(stream as unknown as Record<string, unknown>)[PATCH_FLAG]) {
    proto.stream = wrap(stream, "stream");
    changed = true;
  }
  return changed;
}

function wrap(
  original: (...args: unknown[]) => Promise<unknown>,
  kind: "generate" | "stream",
): (...args: unknown[]) => Promise<unknown> {
  const wrapped = async function wrappedAgentMethod(
    this: AgentProto,
    ...args: unknown[]
  ): Promise<unknown> {
    if (!currentTrace()) return original.apply(this, args);

    const prompt = args[0];
    const options = (args[1] && typeof args[1] === "object") ? args[1] as Record<string, unknown> : {};
    const agentName = this.name ?? "agent";
    const modelId = inferModelId(this);

    const step = openStep("llm-call", `mastra:${agentName}:${modelId}`, {
      inputs: {
        agent: agentName,
        model: modelId,
        method: kind,
        prompt: redact(truncate(prompt)),
        options: redact(summarizeOptions(options)),
      },
      metadata: { framework: "mastra" },
    });

    try {
      const result = await original.apply(this, args);
      if (step) {
        const outputs: Record<string, unknown> = {};
        if (kind === "generate") {
          extractGenerateOutputs(result, outputs);
        } else {
          outputs.streamed = true;
        }
        closeStep(step, { outputs });
      }
      return result;
    } catch (err) {
      if (step) closeStep(step, { error: formatError(err) });
      throw err;
    }
  };
  (wrapped as unknown as Record<string, unknown>)[PATCH_FLAG] = true;
  return wrapped;
}

function inferModelId(agent: AgentProto): string {
  // Mastra agents tend to have either a `model` field or a `_model` private
  // field — try both, fall back to "unknown". Cast to record to read fields
  // not declared in AgentProto's narrower shape.
  const bag = agent as unknown as Record<string, unknown>;
  for (const c of [bag.model, bag._model]) {
    if (typeof c === "string") return c;
    if (c && typeof c === "object") {
      const r = c as Record<string, unknown>;
      const id = r.modelId ?? r.id ?? r.name;
      if (typeof id === "string") return id;
    }
  }
  return "unknown";
}

function summarizeOptions(options: Record<string, unknown>): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const key of ["maxSteps", "maxTokens", "temperature", "topP", "topK"]) {
    if (key in options) out[key] = options[key];
  }
  return out;
}

function extractGenerateOutputs(result: unknown, out: Record<string, unknown>): void {
  if (!result || typeof result !== "object") return;
  const r = result as Record<string, unknown>;
  if (typeof r.text === "string") out.text = truncate(r.text);
  if (typeof r.finishReason === "string") out.finish_reason = r.finishReason;
  const usage = r.usage as Record<string, unknown> | undefined;
  if (usage) {
    out.input_tokens = usage.inputTokens ?? usage.promptTokens ?? null;
    out.output_tokens = usage.outputTokens ?? usage.completionTokens ?? null;
  }
  if (Array.isArray(r.steps)) {
    out.tool_step_count = r.steps.length;
  }
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
