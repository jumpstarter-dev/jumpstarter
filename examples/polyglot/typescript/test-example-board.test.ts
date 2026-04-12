/**
 * Example tests for the generated ExampleBoardDevice wrapper.
 *
 * Run with:
 *   cd python && uv run jmp shell --exporter-config ../examples/polyglot/exporter.yaml \
 *     -- npx vitest run --config ../examples/polyglot/typescript/vitest.config.ts
 */

import * as net from "net";
import { describe, it, expect, beforeAll, afterAll } from "vitest";
import { createExampleBoardDevice } from "./gen/src/testing";
import type { ExampleBoardDevice } from "./gen/src/ExampleBoardDevice";

let device: ExampleBoardDevice;
let close: () => void;

beforeAll(async () => {
  const ctx = await createExampleBoardDevice();
  device = ctx.device;
  close = () => ctx.close();
});

afterAll(() => {
  close?.();
});

// -- Power control --

describe("power", () => {
  it("should turn on", async () => {
    await device.power.on();
  });

  it("should turn off", async () => {
    await device.power.off();
  });

  it("should read power measurements", async () => {
    for await (const reading of device.power.read()) {
      expect(reading.voltage).toBeGreaterThanOrEqual(0);
      expect(reading.current).toBeGreaterThanOrEqual(0);
      break;
    }
  });
});

// -- Storage mux --

describe("storage", () => {
  it("should switch to host", async () => {
    await device.storage.host();
  });

  it("should switch to DUT", async () => {
    await device.storage.dut();
  });

  it("should disconnect", async () => {
    await device.storage.off();
  });
});

// -- Network echo --

describe("network", () => {
  it("should echo bytes through TCP port forward", async () => {
    expect(device.network).toBeDefined();
    const { address, port } = await device.network!.connectTcp();

    const socket = net.createConnection(port, address);
    await new Promise<void>((resolve) => socket.once("connect", resolve));

    socket.write("hello");
    const data = await new Promise<Buffer>((resolve) =>
      socket.once("data", resolve),
    );
    expect(data.toString()).toBe("hello");

    socket.destroy();
    device.network!.close();
  });
});
