# `@loupe/sdk` — TypeScript SDK

Drop-in trace capture for TypeScript/Node LLM agents.

```bash
npm install @loupe/sdk   # not yet published — coming June 2026
```

## Quickstart

```typescript
import { trace } from "@loupe/sdk";

const myAgent = trace({ framework: "vercel-ai-sdk" }, async (query: string) => {
  return await generateText({ model, prompt: query });
});

const result = await myAgent("summarize this PDF");
// trace saved locally; sync to Loupe Cloud with `loupe sync`
```

## Status

🚧 Pre-alpha. Targeting first public release **June 2026**.

See [SPEC.md](../../docs/SPEC.md) for design and roadmap.
