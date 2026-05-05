// Generates TypeScript schema modules from the swagger.json artifacts that
// `buf generate` produces in controller/internal/protocol/...
//
// One TS module is emitted per source swagger document. Multiple admin
// services collide at the path level (every Get/Delete RPC ends up as
// /admin/v1/{name} after google.api.http expansion), so concatenating
// would force openapi-typescript to dedupe paths into name_1, name_2,
// etc. Keeping per-file modules preserves the natural shape: each
// per-resource wrapper imports exactly the schema it needs.
import { mkdir, writeFile, readFile } from "node:fs/promises";
import { existsSync } from "node:fs";
import path from "node:path";
import url from "node:url";
import openapiTS, { astToString, type OpenAPI3 } from "openapi-typescript";
// protoc-gen-openapiv2 emits Swagger 2.0; openapi-typescript only consumes
// OpenAPI 3.x, so we convert in-flight via swagger2openapi.
import { convertObj as swagger2OpenAPI } from "swagger2openapi";

const __dirname = path.dirname(url.fileURLToPath(import.meta.url));
const repoRoot = path.resolve(__dirname, "..", "..", "..");
const swaggerRoot = path.join(repoRoot, "controller", "internal", "protocol", "jumpstarter");
const outDir = path.join(__dirname, "..", "src", "_generated");

// Each entry maps a source .swagger.json to the relative TS module path
// the per-resource wrapper will import.
const sources: { from: string; to: string }[] = [
  { from: "admin/v1/lease.swagger.json", to: "admin/lease.ts" },
  { from: "admin/v1/exporter.swagger.json", to: "admin/exporter.ts" },
  { from: "admin/v1/client.swagger.json", to: "admin/client.ts" },
  { from: "admin/v1/webhook.swagger.json", to: "admin/webhook.ts" },
  { from: "client/v1/client.swagger.json", to: "client/client.ts" },
];

async function generate() {
  for (const s of sources) {
    const abs = path.join(swaggerRoot, s.from);
    if (!existsSync(abs)) {
      console.warn(`skip missing ${abs} — run 'buf generate' in controller/`);
      continue;
    }
    const doc = JSON.parse(await readFile(abs, "utf8"));
    // The @types/swagger2openapi declarations model both a callback-style
    // and a Promise-style overload; the Promise return is genuinely
    // `Promise<ConvertOutputOptions>` but the d.ts unions it with `void`,
    // confusing TS at the call site. Cast through unknown to sidestep
    // both that and the input-type mismatch (Document<{}> from
    // openapi-types vs. openapiTS's internal OpenAPI3 type).
    const converted = (await swagger2OpenAPI(doc as never, { patch: true, warnOnly: true })) as {
      openapi: unknown;
    };
    const ast = await openapiTS(converted.openapi as OpenAPI3);
    const dest = path.join(outDir, s.to);
    await mkdir(path.dirname(dest), { recursive: true });
    await writeFile(dest, `// generated from ${s.from} — do not edit by hand\n${astToString(ast)}`);
  }
  console.log("generated schemas in", outDir);
}

generate().catch((err) => {
  console.error(err);
  process.exit(1);
});
