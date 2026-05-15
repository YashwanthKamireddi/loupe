import { describe, expect, it } from "vitest";

import { redact } from "../src/_redact.js";

describe("redact (TS) — parity with Python loupe._redact", () => {
  it("passes primitives through unchanged", () => {
    expect(redact(null)).toBeNull();
    expect(redact(undefined)).toBeUndefined();
    expect(redact(true)).toBe(true);
    expect(redact(42)).toBe(42);
    expect(redact("hello world")).toBe("hello world");
  });

  it("scrubs known secret-shaped key names regardless of value", () => {
    const input = {
      model: "claude",
      api_key: "sk-ant-abcdefg12345",
      messages: [{ role: "user", content: "hi" }],
    };
    const out = redact(input);
    expect(out.model).toBe("claude");
    expect(out.api_key).toBe("[redacted]");
    expect(out.messages).toEqual([{ role: "user", content: "hi" }]);
  });

  it("handles every common credential-key casing/style", () => {
    const input = {
      Authorization: "Bearer sk-1234567890abcdefgh",
      "X-API-Key": "abc",
      x_auth_token: "xyz",
      "access-key": "k",
      Password: "p",
      secret_key: "s",
      user_authorization_header: "Bearer wat",
      apikey: "k",
    };
    const out = redact(input) as Record<string, unknown>;
    for (const key of Object.keys(input)) {
      expect(out[key]).toBe("[redacted]");
    }
  });

  it("redacts Bearer + provider token patterns inside string values", () => {
    const samples = [
      "Header: Bearer sk-1234567890abcdefghij",
      "use this: sk-ant-abcdefghij1234567890abcdef and we're good",
      "OPENAI=sk-AbCdEfGhIjKlMnOpQrStUv",
      "OPENROUTER=sk-or-AbCdEfGhIjKlMnOpQ",
      "GROQ=gsk_AbCdEfGhIjKlMnOpQrSt12",
      "token=gho_AbCdEfGhIjKlMnOpQrStUvWxYz12",
      "GH PAT: ghp_AbCdEfGhIjKlMnOpQrStUv1234",
      "google: AIzaSyAbCdEfGhIjKlMnOpQrStUv",
      // JWT
      "auth: eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkw.SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c",
    ];
    for (const s of samples) {
      expect(redact(s).includes("[redacted]"), `failed: ${s}`).toBe(true);
    }
  });

  it("walks deeply nested structures", () => {
    const input = {
      level1: {
        level2: [
          { api_key: "should_disappear", value: 1 },
          "Bearer sk-ant-zzzzzzzzzzzzzzz",
        ],
      },
    };
    const out = redact(input) as {
      level1: { level2: [{ api_key: string; value: number }, string] };
    };
    expect(out.level1.level2[0].api_key).toBe("[redacted]");
    expect(out.level1.level2[0].value).toBe(1);
    expect(out.level1.level2[1]).toBe("[redacted]");
  });

  it("is idempotent", () => {
    const input = { authorization: "Bearer abcdefghijklmnop", model: "x" };
    expect(redact(redact(input))).toEqual(redact(input));
  });

  it("does not mutate the input", () => {
    const input: Record<string, unknown> = { api_key: "secret", n: 5 };
    redact(input);
    expect(input.api_key).toBe("secret");
  });

  it("survives deep recursion without crashing", () => {
    let obj: unknown = "leaf";
    for (let i = 0; i < 50; i++) {
      obj = { child: obj };
    }
    expect(() => redact(obj)).not.toThrow();
  });

  it("leaves class instances and non-plain objects alone", () => {
    class Custom { value = 1; }
    const c = new Custom();
    const out = redact({ c }) as { c: Custom };
    expect(out.c).toBe(c);  // pass-through, not walked
  });
});
