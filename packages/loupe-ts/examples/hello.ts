/**
 * The TypeScript counterpart of examples/hello_loupe.py.
 *
 * Run me:
 *   cd packages/loupe-ts
 *   npm install
 *   npm run example:hello
 *   # then back in loupe-py:
 *   loupe list   # shows the TS-captured trace alongside Python ones
 */

import { recordStep, trace } from "../src/index.js";

const fakeCodingAgent = trace(
  { framework: "demo-ts", name: "fake_coding_agent" },
  async (query: string): Promise<string> => {
    recordStep("thought", "plan", {
      outputs: { plan: "1. read 2. diff 3. apply" },
    });

    recordStep("tool-call", "read_file", { inputs: { path: "src/auth.ts" } });
    await new Promise((r) => setTimeout(r, 30));
    recordStep("llm-call", "claude-sonnet-ts", {
      inputs: { prompt: query },
      outputs: { tokens: 980 },
    });

    recordStep("tool-call", "write_file", {
      inputs: { path: "src/auth.ts", diff_lines: 17 },
    });

    recordStep("error", "unguarded-delete", {
      error: "rm -rf src/ instead of src/auth_old.ts",
      metadata: { severity: "critical" },
    });
    throw new Error("agent deleted the wrong path");
  },
);

async function main() {
  try {
    await fakeCodingAgent("refactor auth.ts to use jose");
  } catch (err) {
    console.log("caught (expected):", (err as Error).message);
  }
  console.log("\nrun: loupe list");
}

main();
