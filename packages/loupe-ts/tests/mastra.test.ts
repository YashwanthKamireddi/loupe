import * as fs from "node:fs/promises";
import * as os from "node:os";
import * as path from "node:path";

import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { JSONLStore, trace } from "../src/index.js";
import { patchMastraAgent } from "../src/integrations/mastra.js";

let tempDir: string;
let store: JSONLStore;

beforeEach(async () => {
  tempDir = await fs.mkdtemp(path.join(os.tmpdir(), "loupe-mastra-"));
  store = new JSONLStore(tempDir);
});

afterEach(async () => {
  await fs.rm(tempDir, { recursive: true, force: true });
});

// A minimal Agent class that looks like Mastra's surface area.
class FakeAgent {
  name: string;
  model: { modelId: string };

  constructor(name: string, modelId: string) {
    this.name = name;
    this.model = { modelId };
  }

  async generate(prompt: unknown, _options?: unknown): Promise<{
    text: string;
    finishReason: string;
    usage: { inputTokens: number; outputTokens: number };
    steps: unknown[];
  }> {
    return {
      text: `generated:${String(prompt)}`,
      finishReason: "stop",
      usage: { inputTokens: 12, outputTokens: 3 },
      steps: [{ type: "tool-call" }, { type: "text" }],
    };
  }

  async stream(_prompt: unknown, _options?: unknown): Promise<{ textStream: string }> {
    return { textStream: "fake-stream-handle" };
  }
}

async function readSteps(): Promise<Record<string, unknown>[]> {
  const files = (await fs.readdir(tempDir)).filter((f) => f.endsWith(".jsonl"));
  if (files.length === 0) return [];
  const content = await fs.readFile(path.join(tempDir, files[0]!), "utf-8");
  return content.trim().split("\n").map((l) => JSON.parse(l)).filter((o) => o._type === "step");
}

describe("patchMastraAgent", () => {
  it("captures Agent.generate as a llm-call step", async () => {
    expect(patchMastraAgent(FakeAgent)).toBe(true);
    expect(patchMastraAgent(FakeAgent)).toBe(false); // idempotent

    const agent = new FakeAgent("writer", "claude-haiku-4-5");
    const run = trace({ framework: "mastra-test", store }, async () => {
      return await agent.generate("hello", { maxSteps: 3, temperature: 0.5 });
    });
    const result = await run();
    expect(result.text).toBe("generated:hello");

    const steps = await readSteps();
    expect(steps).toHaveLength(1);
    expect(steps[0]!.kind).toBe("llm-call");
    expect(steps[0]!.name).toBe("mastra:writer:claude-haiku-4-5");
    const inputs = steps[0]!.inputs as Record<string, unknown>;
    expect(inputs.agent).toBe("writer");
    expect(inputs.model).toBe("claude-haiku-4-5");
    expect(inputs.method).toBe("generate");
    expect(inputs.prompt).toBe("hello");
    const opts = inputs.options as Record<string, unknown>;
    expect(opts.maxSteps).toBe(3);
    expect(opts.temperature).toBe(0.5);
    const outputs = steps[0]!.outputs as Record<string, unknown>;
    expect(outputs.text).toBe("generated:hello");
    expect(outputs.finish_reason).toBe("stop");
    expect(outputs.input_tokens).toBe(12);
    expect(outputs.output_tokens).toBe(3);
    expect(outputs.tool_step_count).toBe(2);
  });

  it("captures Agent.stream with streamed: true outputs", async () => {
    patchMastraAgent(FakeAgent);

    const agent = new FakeAgent("streamer", "claude-sonnet-4-6");
    const run = trace({ framework: "mastra-test", store }, async () => {
      return await agent.stream("yo");
    });
    await run();

    const steps = await readSteps();
    expect(steps).toHaveLength(1);
    const inputs = steps[0]!.inputs as Record<string, unknown>;
    expect(inputs.method).toBe("stream");
    expect((steps[0]!.outputs as Record<string, unknown>).streamed).toBe(true);
  });

  it("redacts credentials in prompts before they hit disk", async () => {
    patchMastraAgent(FakeAgent);

    const agent = new FakeAgent("redact-test", "x");
    const run = trace({ framework: "mastra-test", store }, async () => {
      return await agent.generate("paste sk-ant-abcdefghij1234567890abcdef please");
    });
    await run();

    const steps = await readSteps();
    const inputs = steps[0]!.inputs as Record<string, unknown>;
    expect(String(inputs.prompt).includes("[redacted]")).toBe(true);
    expect(String(inputs.prompt).includes("sk-ant-abcdefghij")).toBe(false);
  });

  it("captures errors with the agent identifier", async () => {
    class FailingAgent {
      name = "broken";
      model = { modelId: "x" };
      async generate(_p: unknown): Promise<unknown> {
        throw new Error("agent fell over");
      }
    }
    expect(patchMastraAgent(FailingAgent)).toBe(true);

    const agent = new FailingAgent();
    const run = trace({ framework: "mastra-test", store }, async () => {
      return await agent.generate("hi");
    });
    await expect(run()).rejects.toThrow(/agent fell over/);

    const steps = await readSteps();
    expect(steps).toHaveLength(1);
    expect(steps[0]!.name).toBe("mastra:broken:x");
    expect(String(steps[0]!.error)).toContain("agent fell over");
  });
});
