/**
 * Test helpers for Jumpstarter TypeScript device wrappers.
 *
 * Provides a `createDevice` factory for Jest/Vitest that handles
 * ExporterSession lifecycle in test suites.
 *
 * Usage with Vitest:
 * ```typescript
 * import { createDevice } from "@jumpstarter/client/testing";
 * import { DevBoardDevice } from "./gen/DevBoardDevice";
 *
 * describe("power tests", () => {
 *   const { device, close } = createDevice(DevBoardDevice);
 *
 *   afterAll(async () => { await close(); });
 *
 *   it("should turn on power", async () => {
 *     await device.power.on();
 *   });
 * });
 * ```
 */

import { ExporterSession } from "./ExporterSession";

/**
 * Factory function type for creating a device wrapper from a session.
 */
export type DeviceFactory<T> = new (session: ExporterSession) => T;

/**
 * Test helper result providing a device instance and cleanup function.
 */
export interface DeviceHandle<T> {
  /** The typed device wrapper instance. */
  device: T;
  /** Close the underlying session. Call this in afterAll/afterEach. */
  close: () => Promise<void>;
}

/**
 * Create a typed device wrapper connected to the current `jmp shell` session.
 *
 * This is the primary test helper — call it once per test suite and use the
 * returned device in your tests. The `close()` function should be called in
 * `afterAll` to clean up the gRPC connection.
 *
 * @param WrapperClass - The ExporterClass wrapper constructor (e.g. `DevBoardDevice`)
 * @returns An object with the device instance and a close function
 */
export function createDevice<T>(WrapperClass: DeviceFactory<T>): DeviceHandle<T> {
  const session = ExporterSession.fromEnv();
  const device = new WrapperClass(session);
  return {
    device,
    close: async () => {
      session.close();
    },
  };
}
