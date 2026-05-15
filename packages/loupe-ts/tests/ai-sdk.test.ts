import * as fs from "node:fs/promises";
import * as os from "node:os";
import * as path from "node:path";

import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { JSONLStore, trace } from "../src/index.js";
import { loupeMiddleware, wrapModel } from "../src/integrations/ai-sdk.js";

let tempDir: string;
let store: JSONLStore;

beforeEach(async () => {
  tempDir = await fs.mkdtemp(path.join(os.tmpdir(), "loupe-aisdk-"));
  store = new JSONLStore(tempDir);
});

afterEach(async () => {
  await fs.rm(tempDir, { recursive: true, force: true });
});

/** Minimal fake Vercel AI SDK LanguageModelV2-ish object. */
function makeFakeModel(text = "hi there") {
  return {
    modelId: "fake-claude-1",
    provider: "anthropic-fake",
    async doGenerate(params: { prompt?: string }) {
      return {
        text: `${text}:${params.prompt ?? ""}`,
        finishReason: "stop",
        usage: { inputTokens: 9, outputTokens: 12 },
      };
    },
    async doStream() {
      return { stream: "fake-stream-object" };
    },
  };
}

async function readSteps(): Promise<Record<string, unknown>[]> {
  const files = (await fs.readdir(tempDir)).filter((f) => f.endsWith(".jsonl"));
  if (files.length === 0) return [];
  const content = await fs.readFile(path.join(tempDir, files[0]!), "utf-8");
  return content
    .trim()
    .split("\n")
    .map((l) => JSON.parse(l))
    .filter((o) => o._type === "step");
}

describe("ai-sdk: wrapModel", () => {
  it("captures doGenerate as an llm-call step", async () => {
    const model = wrapModel(makeFakeModel());
    const run = trace({ framework: "ai-sdk", store }, async () => {
      return await model.doGenerate!({ prompt: "yo" });
    });
    const result = (await run()) as { text: string };
    expect(result.text).toBe("hi there:yo");

    const steps = await readSteps();
    expect(steps).toHaveLength(1);
    expect(steps[0]!.kind).toBe("llm-call");
    expect(steps[0]!.name).toBe("ai-sdk:fake-claude-1");
    expect((steps[0]!.outputs as Record<string, unknown>).text).toBe("hi there:yo");
    expect((steps[0]!.outputs as Record<string, unknown>).input_tokens).toBe(9);
    expect((steps[0]!.outputs as Record<string, unknown>).output_tokens).toBe(12);
    expect((steps[0]!.outputs as Record<string, unknown>).finish_reason).toBe("stop");
  });

  it("captures errors with the model identifier", async () => {
    const model = wrapModel({
      modelId: "boom-model",
      provider: "p",
      async doGenerate(_params: { prompt?: string }) {
        throw new Error("rate limited");
      },
    });
    const run = trace({ framework: "ai-sdk", store }, async () => {
      await model.doGenerate!({ prompt: "x" });
    });
    await expect(run()).rejects.toThrow(/rate limited/);

    const steps = await readSteps();
    expect(steps).toHaveLength(1);
    expect(steps[0]!.error).toMatch(/rate limited/);
  });

  it("captures doStream as a streaming llm-call step", async () => {
    const model = wrapModel(makeFakeModel());
    const run = trace({ framework: "ai-sdk", store }, async () => {
      return await model.doStream!();
    });
    const result = (await run()) as { stream: string };
    expect(result.stream).toBe("fake-stream-object");

    const steps = await readSteps();
    expect(steps).toHaveLength(1);
    expect((steps[0]!.outputs as Record<string, unknown>).stream).toBe("started");
    expect((steps[0]!.metadata as Record<string, unknown>).streaming).toBe(true);
  });
});

describe("ai-sdk: loupeMiddleware", () => {
  it("wraps a middleware-style doGenerate", async () => {
    const mw = loupeMiddleware();
    const run = trace({ framework: "ai-sdk-mw", store }, async () => {
      return await mw.wrapGenerate({
        params: { model: "mw-model", prompt: "hello", temperature: 0.5 },
        doGenerate: async () => ({
          text: "ok",
          finishReason: "stop",
          usage: { inputTokens: 1, outputTokens: 2 },
        }),
      });
    });
    await run();
    const steps = await readSteps();
    expect(steps).toHaveLength(1);
    expect(steps[0]!.name).toBe("ai-sdk:mw-model");
    expect((steps[0]!.outputs as Record<string, unknown>).text).toBe("ok");
  });
});
