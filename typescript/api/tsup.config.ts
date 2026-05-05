import { defineConfig } from "tsup";

// One output file per subpath export so consumers tree-shake at the
// per-resource boundary. Keeping each entry tiny matters most for browser
// bundle size in the planned standalone web UI / Backstage plug-in.
export default defineConfig({
  entry: {
    "admin/lease": "src/admin/lease.ts",
    "admin/exporter": "src/admin/exporter.ts",
    "admin/client": "src/admin/client.ts",
    "admin/webhook": "src/admin/webhook.ts",
    "client/lease": "src/client/lease.ts",
    "client/exporter": "src/client/exporter.ts",
  },
  format: ["esm", "cjs"],
  dts: true,
  clean: true,
  splitting: false,
  sourcemap: true,
  target: "es2022",
});
