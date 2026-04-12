/**
 * Shared protobuf encoding/decoding and gRPC stream helpers for
 * RouterService.Stream port-forward adapters.
 */

import * as grpc from "@grpc/grpc-js";
import { StreamChannel } from "./StreamChannel";
import type { ExporterSession } from "./ExporterSession";

/** Service path for RouterService.Stream */
const STREAM_METHOD = "/jumpstarter.v1.RouterService/Stream";

/**
 * Open a StreamChannel to a driver method via RouterService.Stream.
 *
 * Sets the `"request"` metadata key to a JSON-serialized DriverStreamRequest,
 * matching the Python wire format.
 */
export function openDriverStream(
  session: ExporterSession,
  driverUuid: string,
  method: string,
): { channel: StreamChannel; grpcClient: grpc.Client } {
  const grpcClient = new grpc.Client(
    session.getAddress(),
    session.getCredentials(),
    {},
  );

  const metadata = new grpc.Metadata();
  metadata.set(
    "request",
    JSON.stringify({ kind: "driver", uuid: driverUuid, method }),
  );

  const stream = grpcClient.makeBidiStreamRequest(
    STREAM_METHOD,
    (value: any) => encodeStreamFrame(value.payload, value.frameType),
    (buffer: Buffer) => decodeStreamFrame(buffer),
    metadata,
  );

  return { channel: StreamChannel.fromStream(stream), grpcClient };
}

/** Encode a StreamRequest frame to protobuf wire format. */
export function encodeStreamFrame(payload: Uint8Array, frameType: number): Buffer {
  const parts: Buffer[] = [];

  if (payload.length > 0) {
    parts.push(Buffer.from([0x0a])); // field 1, wire type 2
    parts.push(encodeVarint(payload.length));
    parts.push(Buffer.from(payload));
  }

  if (frameType !== 0) {
    parts.push(Buffer.from([0x10])); // field 2, wire type 0
    parts.push(encodeVarint(frameType));
  }

  return Buffer.concat(parts);
}

/** Decode a StreamResponse frame from protobuf wire format. */
export function decodeStreamFrame(buffer: Buffer): {
  payload: Uint8Array;
  frameType: number;
} {
  let payload: Uint8Array = new Uint8Array(0);
  let frameType = 0;
  let offset = 0;

  while (offset < buffer.length) {
    const [tag, newOffset] = readVarint(buffer, offset);
    offset = newOffset;
    const fieldNumber = tag >>> 3;
    const wireType = tag & 0x07;

    if (fieldNumber === 1 && wireType === 2) {
      const [len, lenOffset] = readVarint(buffer, offset);
      offset = lenOffset;
      payload = buffer.subarray(offset, offset + len);
      offset += len;
    } else if (fieldNumber === 2 && wireType === 0) {
      const [val, valOffset] = readVarint(buffer, offset);
      offset = valOffset;
      frameType = val;
    } else {
      if (wireType === 0) {
        const [, o] = readVarint(buffer, offset);
        offset = o;
      } else if (wireType === 2) {
        const [len, o] = readVarint(buffer, offset);
        offset = o + len;
      } else if (wireType === 1) {
        offset += 8;
      } else if (wireType === 5) {
        offset += 4;
      }
    }
  }

  return { payload, frameType };
}

function encodeVarint(value: number): Buffer {
  const bytes: number[] = [];
  while (value > 0x7f) {
    bytes.push((value & 0x7f) | 0x80);
    value >>>= 7;
  }
  bytes.push(value & 0x7f);
  return Buffer.from(bytes);
}

function readVarint(buffer: Buffer, offset: number): [number, number] {
  let result = 0;
  let shift = 0;
  while (offset < buffer.length) {
    const byte = buffer[offset++];
    result |= (byte & 0x7f) << shift;
    if ((byte & 0x80) === 0) return [result, offset];
    shift += 7;
  }
  return [result, offset];
}
