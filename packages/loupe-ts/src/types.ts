/**
 * Wire-format types — identical to the Python loupe package.
 *
 * Every Step and Trace serializes to the same JSONL schema as loupe-py so the
 * same `~/.loupe/traces/*.jsonl` directory is interchangeable across languages.
 */

export type StepKind =
  | "llm-call"
  | "tool-call"
  | "io"
  | "thought"
  | "error"
  | "custom";

export interface Step {
  step_id: string;
  parent_step_id: string | null;
  kind: StepKind;
  name: string;
  started_at: number; // unix seconds, float
  ended_at: number | null;
  inputs: Record<string, unknown>;
  outputs: Record<string, unknown>;
  metadata: Record<string, unknown>;
  error: string | null;
}

export interface Trace {
  trace_id: string;
  name: string;
  framework: string | null;
  started_at: number;
  ended_at: number | null;
  steps: Step[];
  metadata: Record<string, unknown>;
}

export interface TraceOptions {
  name?: string;
  framework?: string;
  store?: TraceStore;
}

export interface TraceStore {
  save(trace: Trace): Promise<void>;
}

export interface RecordStepOptions {
  inputs?: Record<string, unknown>;
  outputs?: Record<string, unknown>;
  metadata?: Record<string, unknown>;
  error?: string | null;
  parentStepId?: string | null;
}
