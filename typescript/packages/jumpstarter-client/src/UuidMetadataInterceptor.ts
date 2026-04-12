/**
 * gRPC client interceptor that injects the `x-jumpstarter-driver-uuid`
 * metadata header into every outgoing call. This routes the call to the
 * correct driver instance within the exporter.
 */

import { Metadata } from "@grpc/grpc-js";

const UUID_KEY = "x-jumpstarter-driver-uuid";

/**
 * Create a gRPC interceptor that injects the driver instance UUID into
 * every outgoing call's metadata.
 *
 * @param uuid - The driver instance UUID to inject
 * @returns A gRPC interceptor function compatible with `@grpc/grpc-js`
 */
export function createUuidInterceptor(
  uuid: string,
): (options: any, nextCall: Function) => any {
  return (options: any, nextCall: Function) => {
    const metadata: Metadata = options.metadata ?? new Metadata();
    metadata.set(UUID_KEY, uuid);
    return nextCall({ ...options, metadata });
  };
}

/**
 * Create gRPC channel options with a UUID metadata interceptor.
 *
 * @param uuid - The driver instance UUID
 * @returns Interceptor array suitable for passing to gRPC client constructors
 */
export function uuidInterceptors(uuid: string) {
  return [createUuidInterceptor(uuid)];
}
