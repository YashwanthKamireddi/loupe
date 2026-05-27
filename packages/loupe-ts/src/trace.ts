/**
 * `trace()` higher-order function — the TypeScript entry point.
 *
 * Wraps any async function so every run is captured as a Loupe Trace. Uses
 * Node's AsyncLocalStorage to propagate the current trace through asynchronous
 * call stacks, mirroring Python's ContextVar.
 */

import { AsyncLocalStorage } from "node:async_hooks";
import { randomUUID } from "node:crypto";

import { defaultStore } from "./store.js";
import type {
  RecordStepOptions,
  Step,
  StepKind,
  Trace,
  TraceOptions,
  TraceStore,
} from "./types.js";

const _als = new AsyncLocalStorage<Trace>();

export function currentTrace(): Trace | undefined {
  return _als.getStore();
}

/**
 * Internal: run `fn` inside an implicit one-call trace context.
 *
 * Used by the autopatch path in `universal.ts` so a fetch call made
 * OUTSIDE any user-defined `@trace` block still produces a single-step
 * trace on disk. Mirrors `loupe.integrations.httpx._implicit_trace_context`
 * in the Python SDK.
 *
 * Marked underscore-prefixed because it is a stability-sensitive
 * internal — public callers should keep using `trace(...)`.
 */
export async function _runImplicitTrace<T>(
  options: { name?: string; framework?: string },
  fn: () => Promise<T>,
): Promise<T> {
  const t: Trace = {
    trace_id: randomUUID().replace(/-/g, ""),
    name: options.name ?? "auto",
    framework: options.framework ?? "autopatch",
    started_at: Date.now() / 1000,
    ended_at: null,
    steps: [],
    metadata: {},
  };
  const store = defaultStore();
  try {
    return await _als.run(t, fn);
  } catch (err) {
    t.metadata.failed = true;
    t.metadata.error = formatError(err);
    throw err;
  } finally {
    t.ended_at = Date.now() / 1000;
    await store.save(t);
  }
}

export function recordStep(
  kind: StepKind,
  name: string,
  options: RecordStepOptions = {},
): Step | null {
  const t = currentTrace();
  if (!t) return null;
  const now = Date.now() / 1000;
  const step: Step = {
    step_id: randomUUID().replace(/-/g, "").slice(0, 12),
    parent_step_id: options.parentStepId ?? null,
    kind,
    name,
    started_at: now,
    ended_at: now,
    inputs: options.inputs ?? {},
    outputs: options.outputs ?? {},
    metadata: options.metadata ?? {},
    error: options.error ?? null,
  };
  t.steps.push(step);
  return step;
}

export function openStep(
  kind: StepKind,
  name: string,
  options: Omit<RecordStepOptions, "error"> = {},
): Step | null {
  const t = currentTrace();
  if (!t) return null;
  const step: Step = {
    step_id: randomUUID().replace(/-/g, "").slice(0, 12),
    parent_step_id: options.parentStepId ?? null,
    kind,
    name,
    started_at: Date.now() / 1000,
    ended_at: null,
    inputs: options.inputs ?? {},
    outputs: {},
    metadata: options.metadata ?? {},
    error: null,
  };
  t.steps.push(step);
  return step;
}

export function closeStep(
  step: Step,
  result?: { outputs?: Record<string, unknown>; error?: string | null },
): void {
  step.ended_at = Date.now() / 1000;
  if (result?.outputs) step.outputs = { ...step.outputs, ...result.outputs };
  if (result?.error !== undefined) step.error = result.error;
}

/**
 * Wrap an async function so every call is captured into a Trace.
 *
 * @example
 *   const myAgent = trace({ framework: "vercel-ai-sdk" }, async (q: string) => {
 *     return await generateText({ model, prompt: q });
 *   });
 */
export function trace<TArgs extends unknown[], TResult>(
  options: TraceOptions,
  fn: (...args: TArgs) => Promise<TResult>,
): (...args: TArgs) => Promise<TResult>;

export function trace<TArgs extends unknown[], TResult>(
  fn: (...args: TArgs) => Promise<TResult>,
): (...args: TArgs) => Promise<TResult>;

export function trace<TArgs extends unknown[], TResult>(
  optionsOrFn:
    | TraceOptions
    | ((...args: TArgs) => Promise<TResult>),
  maybeFn?: (...args: TArgs) => Promise<TResult>,
): (...args: TArgs) => Promise<TResult> {
  const isShortForm = typeof optionsOrFn === "function";
  const options: TraceOptions = isShortForm ? {} : optionsOrFn;
  const fn = isShortForm ? optionsOrFn : maybeFn!;
  const name = options.name ?? fn.name ?? "agent";
  const framework = options.framework ?? null;
  const store: TraceStore = options.store ?? defaultStore();

  return async function tracedFn(...args: TArgs): Promise<TResult> {
    const t: Trace = {
      trace_id: randomUUID().replace(/-/g, ""),
      name,
      framework,
      started_at: Date.now() / 1000,
      ended_at: null,
      steps: [],
      metadata: {},
    };

    try {
      return await _als.run(t, () => fn(...args));
    } catch (err) {
      t.metadata.failed = true;
      t.metadata.error = formatError(err);
      throw err;
    } finally {
      t.ended_at = Date.now() / 1000;
      await store.save(t);
    }
  };
}

function formatError(err: unknown): string {
  if (err instanceof Error) {
    return `${err.constructor.name}: ${err.message}`;
  }
  return String(err);
}
