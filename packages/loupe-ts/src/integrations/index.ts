/**
 * Single-import entry point for every TypeScript integration.
 *
 * Today this re-exports the two integrations + a `patchAll()` helper that
 * turns on every one whose dependency is installed (mirror of the Python
 * `loupe.integrations.patch_all`).
 *
 * @example
 *   import { patchAll } from "loupe-ai/integrations";
 *   const report = await patchAll();   // { "universal-fetch": true }
 *
 * Double-capture avoidance
 * ------------------------
 * Direct SDK integrations (currently `wrapModel` for Vercel AI SDK) and
 * `patchFetch` both see the same network call when active together. To
 * avoid emitting two Steps per logical call, direct integrations call
 * `withSuppressedHttpCapture(fn)` around the wrapped call; `patchFetch`
 * reads the flag and skips. Implemented with AsyncLocalStorage so async
 * tasks each see their own state.
 */

import { AsyncLocalStorage } from "node:async_hooks";

import { patchFetch } from "./universal.js";

export { patchFetch, wrapFetch } from "./universal.js";
export { loupeMiddleware, wrapModel } from "./ai-sdk.js";

const _captureStorage = new AsyncLocalStorage<{ direct: boolean }>();

/** Read the current direct-capture flag. */
export function isDirectCaptureActive(): boolean {
  return _captureStorage.getStore()?.direct === true;
}

/**
 * Run `fn` with the direct-capture flag set so `patchFetch` skips
 * emitting a Step (the SDK-level integration is already capturing).
 *
 * Returns whatever `fn` returns. Safe to nest.
 */
export function withSuppressedHttpCapture<T>(fn: () => T): T {
  return _captureStorage.run({ direct: true }, fn);
}

/**
 * Turn on every integration whose dependency is installed. Idempotent.
 *
 * Returns a record mapping integration name → bool (true if patched this
 * call, false if it was already patched). Integrations whose dependency
 * isn't available are absent entirely.
 *
 * For the TS SDK this is currently small — Vercel AI SDK is opt-in via
 * `wrapModel(...)` not a global patch, so the only "always-on" patch is
 * the universal fetch one. The shape matches Python `patch_all()` so
 * cross-language code can rely on the same contract.
 */
export async function patchAll(): Promise<Record<string, boolean>> {
  const report: Record<string, boolean> = {};

  // Universal fetch: capture every LLM provider call regardless of SDK.
  // Always available — globalThis.fetch is built-in on Node 18+ and browsers.
  if (typeof (globalThis as { fetch?: unknown }).fetch === "function") {
    report["universal-fetch"] = patchFetch();
  }

  return report;
}
