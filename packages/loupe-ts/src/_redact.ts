/**
 * Secret redaction for captured request data — TypeScript mirror of the
 * Python `loupe._redact` module. Both implementations MUST behave identically
 * so a trace looks the same whether it was captured by the Python SDK, the
 * TypeScript SDK, or POSTed in directly.
 *
 * Stability rules (same as Python):
 * - Never throw on malformed input — the redactor must never crash a trace.
 * - Return the same shape as the input (object → object, array → array, string → string).
 * - Idempotent: redact(redact(x)) === redact(x).
 * - Non-mutating: the input is never modified.
 */

// Field names that ALWAYS get scrubbed, regardless of value.
const SECRET_NAME_PATTERN = new RegExp(
  "(authorization|api[-_]?key|apikey|secret|token|password|bearer|" +
    "private[-_]?key|access[-_]?key|x[-_]?auth)",
  "i",
);

// Substrings that, when seen inside a string value, indicate a credential.
const SECRET_VALUE_PATTERNS: RegExp[] = [
  /\bBearer\s+[A-Za-z0-9_\-./+=]{8,}/gi,
  /\bsk-[A-Za-z0-9_-]{16,}/g,
  /\bsk-ant-[A-Za-z0-9_-]{20,}/g,
  /\bsk-or-[A-Za-z0-9_-]{16,}/g,
  /\bgsk_[A-Za-z0-9_-]{20,}/g,
  /\bgho_[A-Za-z0-9_]{20,}/g,
  /\bghp_[A-Za-z0-9_]{20,}/g,
  /\bAIza[A-Za-z0-9_-]{20,}/g,
  // JWT (three base64url segments, each ≥ 8 chars)
  /\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}/g,
];

const REDACTED = "[redacted]";
const MAX_DEPTH = 8;

/**
 * Return a deeply-redacted copy of `value`. The original is not modified.
 *
 * Primitives pass through (strings get pattern-scanned). Plain objects have
 * known-secret keys replaced with "[redacted]". Arrays are walked element-wise.
 * Unknown types (functions, dates, class instances) pass through as-is.
 */
export function redact<T>(value: T): T {
  return walk(value, 0) as T;
}

function walk(value: unknown, depth: number): unknown {
  if (depth > MAX_DEPTH) return value;
  if (value === null || value === undefined) return value;

  const t = typeof value;
  if (t === "boolean" || t === "number") return value;
  if (t === "string") return scrubString(value as string);

  if (Array.isArray(value)) {
    return value.map((item) => walk(item, depth + 1));
  }

  // Plain objects only — leave Dates, RegExps, class instances alone.
  if (t === "object" && isPlainObject(value as object)) {
    const out: Record<string, unknown> = {};
    for (const [k, v] of Object.entries(value as object)) {
      out[k] = SECRET_NAME_PATTERN.test(k) ? REDACTED : walk(v, depth + 1);
    }
    return out;
  }
  return value;
}

function scrubString(s: string): string {
  let out = s;
  for (const pattern of SECRET_VALUE_PATTERNS) {
    out = out.replace(pattern, REDACTED);
  }
  return out;
}

function isPlainObject(o: object): boolean {
  const proto = Object.getPrototypeOf(o);
  return proto === Object.prototype || proto === null;
}
