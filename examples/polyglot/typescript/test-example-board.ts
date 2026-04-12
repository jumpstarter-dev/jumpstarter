/**
 * End-to-end test of the generated TypeScript typed client.
 *
 * Run with:
 *   cd python && uv run jmp shell --exporter-config ../examples/polyglot/exporter.yaml \
 *     -- npx tsx ../examples/polyglot/typescript/test-example-board.ts
 */

import { createExampleBoardDevice } from "./gen/src/testing";

async function main() {
  const ctx = await createExampleBoardDevice();
  const device = ctx.device;

  console.log("Device created successfully\n");

  // -- Power tests --
  console.log("--- Power Tests ---");

  await device.power.on();
  console.log("power.on(): OK");

  await device.power.off();
  console.log("power.off(): OK");

  for await (const reading of device.power.read()) {
    console.log(`power.read(): voltage=${reading.voltage}, current=${reading.current}`);
    break; // Just verify the first reading
  }

  // -- Storage Mux tests --
  console.log("\n--- Storage Mux Tests ---");

  await device.storage.host();
  console.log("storage.host(): OK");

  await device.storage.dut();
  console.log("storage.dut(): OK");

  await device.storage.off();
  console.log("storage.off(): OK");

  // -- Optional Network --
  console.log("\n--- Optional Network ---");
  if (device.network) {
    console.log("network driver available");
  } else {
    console.log("network driver is undefined (optional)");
  }

  console.log("\n=== All TypeScript tests PASSED! ===");
  ctx.close();
  process.exit(0);
}

main().catch((err) => {
  console.error("Test failed:", err);
  process.exit(1);
});
