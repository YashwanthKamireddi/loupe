/**
 * Persistent storage for Loupe traces (TypeScript counterpart of loupe-py's store).
 *
 * Default: append-only JSONL files under `~/.loupe/traces/{trace_id}.jsonl`,
 * matching the schema documented in the Python README. Set `LOUPE_HOME` to
 * override the root.
 */

import * as fs from "node:fs/promises";
import * as os from "node:os";
import * as path from "node:path";

import type { Step, Trace, TraceStore } from "./types.js";

export function loupeHome(): string {
  return process.env.LOUPE_HOME ?? path.join(os.homedir(), ".loupe");
}

export class JSONLStore implements TraceStore {
  readonly root: string;

  constructor(root?: string) {
    this.root = root ?? path.join(loupeHome(), "traces");
  }

  async save(trace: Trace): Promise<void> {
    await fs.mkdir(this.root, { recursive: true });
    const filePath = path.join(this.root, `${trace.trace_id}.jsonl`);

    // Match the Python wire format exactly:
    //  - line 0: { _type: "trace", ...trace WITHOUT steps }
    //  - lines 1..N: { _type: "step", ...step }
    const { steps, ...header } = trace;
    const lines: string[] = [
      JSON.stringify({ _type: "trace", ...header }),
      ...steps.map((s: Step) => JSON.stringify({ _type: "step", ...s })),
    ];
    await fs.writeFile(filePath, lines.join("\n") + "\n", "utf-8");
  }
}

let _default: TraceStore | null = null;

export function defaultStore(): TraceStore {
  if (_default === null) {
    _default = new JSONLStore();
  }
  return _default;
}

/** For tests: reset the cached default so a fresh LOUPE_HOME is picked up. */
export function _resetDefaultStore(): void {
  _default = null;
}
