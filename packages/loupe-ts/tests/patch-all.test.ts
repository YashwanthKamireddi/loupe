import { describe, expect, it } from "vitest";

import { patchAll } from "../src/integrations/index.js";

describe("patchAll (TS) — mirror of Python loupe.integrations.patch_all", () => {
  it("returns a record on the first call", async () => {
    const report = await patchAll();
    expect(typeof report).toBe("object");
    expect(report).not.toBeNull();
  });

  it("includes universal-fetch when globalThis.fetch is present", async () => {
    expect(typeof globalThis.fetch).toBe("function");
    const report = await patchAll();
    expect("universal-fetch" in report).toBe(true);
  });

  it("is idempotent — calling twice never patches universal-fetch again", async () => {
    await patchAll();
    const second = await patchAll();
    if ("universal-fetch" in second) {
      expect(second["universal-fetch"]).toBe(false);
    }
  });

  it("never includes integrations for missing deps", async () => {
    const report = await patchAll();
    expect("definitely-not-a-real-package" in report).toBe(false);
  });
});
