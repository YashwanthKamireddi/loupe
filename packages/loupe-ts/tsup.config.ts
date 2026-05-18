import { defineConfig } from "tsup";

export default defineConfig({
  entry: {
    index: "src/index.ts",
    "integrations/index": "src/integrations/index.ts",
    "integrations/ai-sdk": "src/integrations/ai-sdk.ts",
    "integrations/universal": "src/integrations/universal.ts",
    "integrations/mastra": "src/integrations/mastra.ts",
  },
  format: ["esm", "cjs"],
  dts: true,
  sourcemap: true,
  clean: true,
  splitting: false,
  treeshake: true,
  target: "node20",
  external: ["ai"],
});
