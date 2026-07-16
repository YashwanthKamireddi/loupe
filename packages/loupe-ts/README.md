# `loupe-ai` — TypeScript SDK

A magnifying glass for your AI agent. **TypeScript counterpart of the [Python `loupe-ai`](https://pypi.org/project/loupe-ai/) package** — same wire format, same `~/.loupe/traces/` directory, same `loupe ui` dashboard.

```bash
npm install loupe-ai
```

## Quickstart

```typescript
import { trace, recordStep } from "loupe-ai";

const myAgent = trace({ framework: "ai-sdk" }, async (q: string) => {
  recordStep("thought", "plan", { outputs: { plan: "..." } });
  return await someLLMCall(q);
});

await myAgent("refactor auth.ts");
// trace written to ~/.loupe/traces/{trace_id}.jsonl
```

Then in another terminal:

```bash
pip install loupe-ai
loupe ui   # forensic dashboard shows your TS trace
```

## Vercel AI SDK integration

```typescript
import { generateText } from "ai";
import { anthropic } from "@ai-sdk/anthropic";
import { trace } from "loupe-ai";
import { wrapModel } from "loupe-ai/ai-sdk";

const model = wrapModel(anthropic("claude-sonnet-4-6"));

const myAgent = trace({ framework: "ai-sdk" }, async (q: string) => {
  const { text } = await generateText({ model, prompt: q });
  return text;
});
```

Or middleware-style:

```typescript
import { wrapLanguageModel } from "ai";
import { loupeMiddleware } from "loupe-ai/ai-sdk";

const model = wrapLanguageModel({
  model: anthropic("claude-sonnet-4-6"),
  middleware: loupeMiddleware(),
});
```

## Zero-code capture

No imports at all — preload the autopatch hook and every supported SDK call in the process is traced:

```bash
NODE_OPTIONS="--require loupe-ai/autopatch" node agent.js
```

## Dev setup (from the repo)

```bash
cd packages/loupe-ts
npm install
npm test          # vitest
npm run build     # tsup → dist/
```
