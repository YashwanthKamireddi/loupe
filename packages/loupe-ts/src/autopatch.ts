/**
 * `loupe-ai/autopatch` — zero-code activation of universal fetch capture.
 *
 * Require this module at Node startup and Loupe activates capture for every
 * fetch call to a known LLM provider:
 *
 *     export NODE_OPTIONS="--require loupe-ai/autopatch"
 *     node my-agent.js          # captured automatically
 *
 * Activation rules (mirrors the Python `.pth` hook):
 *
 *   1. `LOUPE_AUTOPATCH=0` / `false` / `no` / `off`  → never activate.
 *   2. `LOUPE_AUTOPATCH=1` / `true` / `yes` / `on`   → activate now.
 *   3. Env var unset:
 *        • `~/.loupe/config.toml` exists → activate (you ran `loupe setup`).
 *        • Otherwise                     → do nothing (transitive install).
 *
 * Cost when off: one env lookup + one `existsSync` ≈ a few µs. No imports
 * past this module's top level, no globals touched.
 *
 * A broken Loupe install must never break Node startup — every exception
 * below is swallowed silently.
 */

import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";

import { patchFetch } from "./integrations/universal.js";

const TRUTHY = new Set(["1", "true", "yes", "on"]);
const FALSY = new Set(["0", "false", "no", "off", ""]);

function shouldActivate(): boolean {
  const raw =
    typeof process !== "undefined" && process?.env?.LOUPE_AUTOPATCH !== undefined
      ? process.env.LOUPE_AUTOPATCH ?? ""
      : null;
  if (raw !== null) {
    const norm = raw.toLowerCase();
    if (TRUTHY.has(norm)) return true;
    if (FALSY.has(norm)) return false;
    return false; // unknown value → safer default is off
  }
  // Env var unset → activate iff the user has run `loupe setup`.
  try {
    const home = process.env.LOUPE_HOME ?? path.join(os.homedir(), ".loupe");
    return fs.existsSync(path.join(home, "config.toml"));
  } catch {
    return false;
  }
}

if (shouldActivate()) {
  try {
    patchFetch();
  } catch {
    // never crash Node startup — capture is a best-effort observability layer
  }
}

// Re-export so callers can import explicitly if they prefer a function call
// over the env-var trigger.
export { patchFetch };
