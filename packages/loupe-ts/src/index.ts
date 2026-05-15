/**
 * Loupe — a magnifying glass for your AI agent.
 *
 * TypeScript SDK. Same wire format as `loupe` Python package: both write
 * `~/.loupe/traces/{trace_id}.jsonl` and show up together in `loupe ui`.
 *
 * @example
 *   import { trace, recordStep } from "@loupe/sdk";
 *
 *   const myAgent = trace({ framework: "ai-sdk" }, async (q: string) => {
 *     recordStep("thought", "plan", { outputs: { plan: "..." } });
 *     return await someLLMCall(q);
 *   });
 */

export {
  closeStep,
  currentTrace,
  openStep,
  recordStep,
  trace,
} from "./trace.js";

export { JSONLStore, defaultStore, loupeHome } from "./store.js";

export type {
  RecordStepOptions,
  Step,
  StepKind,
  Trace,
  TraceOptions,
  TraceStore,
} from "./types.js";

export const VERSION = "0.0.9";
