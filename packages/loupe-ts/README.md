# `@loupe/sdk` — TypeScript SDK

A magnifying glass for your AI agent. **TypeScript counterpart of the [Python `loupe`](../loupe-py) package** — same wire format, same `~/.loupe/traces/` directory, same `loupe ui` dashboard.

```bash
npm install @loupe/sdk          # not yet published; use file: until v0.1
```

## Quickstart

```typescript
import { trace, recordStep } from "@loupe/sdk";

const myAgent = trace({ framework: "ai-sdk" }, async (q: string) => {
  recordStep("thought", "plan", { outputs: { plan: "..." } });
  return await someLLMCall(q);
});

await myAgent("refactor auth.ts");
// trace written to ~/.loupe/traces/{trace_id}.jsonl
```

Then in another terminal:

```bash
pip install 'loupe[ui]'
loupe ui   # forensic dashboard shows your TS trace
```

## Vercel AI SDK integration

```typescript
import { generateText } from "ai";
import { anthropic } from "@ai-sdk/anthropic";
import { trace } from "@loupe/sdk";
import { wrapModel } from "@loupe/sdk/ai-sdk";

const model = wrapModel(anthropic("claude-sonnet-4-6"));

const myAgent = trace({ framework: "ai-sdk" }, async (q: string) => {
  const { text } = await generateText({ model, prompt: q });
  return text;
});
```

Or middleware-style:

```typescript
import { wrapLanguageModel } from "ai";
import { loupeMiddleware } from "@loupe/sdk/ai-sdk";

const model = wrapLanguageModel({
  model: anthropic("claude-sonnet-4-6"),
  middleware: loupeMiddleware(),
});
```

## Status

🚧 Pre-alpha. First public release targeted **June 2026**.

## Dev setup

```bash
cd packages/loupe-ts
npm install
npm test          # vitest
npm run build     # tsup → dist/
npm run example:hello
```
