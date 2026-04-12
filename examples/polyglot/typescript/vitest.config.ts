import { defineConfig } from "vitest/config";
import * as path from "path";

export default defineConfig({
  test: {
    root: path.dirname(new URL(import.meta.url).pathname),
    include: ["*.test.ts"],
    testTimeout: 30000,
  },
});
