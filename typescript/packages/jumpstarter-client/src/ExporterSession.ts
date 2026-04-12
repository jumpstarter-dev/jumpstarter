/**
 * A session connected to a Jumpstarter exporter inside a `jmp shell`.
 *
 * Usage:
 * ```typescript
 * const session = ExporterSession.fromEnv();
 * const report = await session.getReport();
 * const power = report.requireByName("power");
 * // Use native gRPC stubs with session.createChannel(power.uuid)
 * session.close();
 * ```
 */

import * as grpc from "@grpc/grpc-js";
import { DriverReport } from "./DriverReport";
import { createUuidInterceptor } from "./UuidMetadataInterceptor";

const ENV_HOST = "JUMPSTARTER_HOST";

export class ExporterSession {
  private readonly channel: grpc.Channel;
  private readonly credentials: grpc.ChannelCredentials;
  private readonly address: string;
  private cachedReport: DriverReport | null = null;

  private constructor(address: string, credentials: grpc.ChannelCredentials) {
    this.address = address;
    this.credentials = credentials;
    this.channel = new grpc.Channel(address, credentials, {});
  }

  /**
   * Connect to an exporter using the `JUMPSTARTER_HOST` environment variable
   * set by `jmp shell`.
   *
   * Supports both Unix domain sockets and TCP addresses.
   *
   * @returns An exporter session connected via the shell's socket
   * @throws Error if JUMPSTARTER_HOST is not set
   */
  static fromEnv(): ExporterSession {
    const host = process.env[ENV_HOST];
    if (!host) {
      throw new Error(
        "JUMPSTARTER_HOST environment variable is not set. " +
          "Are you running inside a 'jmp shell' session?",
      );
    }
    const address = isTcpAddress(host) ? host : `unix:${host}`;
    return new ExporterSession(address, grpc.credentials.createInsecure());
  }

  /**
   * Get the address used to connect to the exporter.
   * This is the resolved address (TCP or unix: prefixed).
   */
  getAddress(): string {
    return this.address;
  }

  /**
   * Get the channel credentials (insecure for jmp shell sessions).
   */
  getCredentials(): grpc.ChannelCredentials {
    return this.credentials;
  }

  /**
   * Get the device report from the exporter.
   *
   * The report describes the driver instance tree: each driver's UUID, labels,
   * description, and available methods. Results are cached after the first call.
   *
   * @returns The driver report
   */
  async getReport(): Promise<DriverReport> {
    if (this.cachedReport) {
      return this.cachedReport;
    }

    const client = new grpc.Client(this.address, this.credentials, {});
    const response = await new Promise<any>((resolve, reject) => {
      client.makeUnaryRequest(
        "/jumpstarter.v1.ExporterService/GetReport",
        (_value: any) => Buffer.alloc(0),
        (buffer: Buffer) => decodeGetReportResponse(buffer),
        {},
        (err: grpc.ServiceError | null, resp: any) => {
          if (err) reject(err);
          else resolve(resp);
        },
      );
    });

    this.cachedReport = new DriverReport(response);
    return this.cachedReport;
  }

  /**
   * Look up a required driver by name and return its UUID.
   *
   * @param name - The driver name (`jumpstarter.dev/name` label value)
   * @returns The driver instance UUID
   * @throws Error if no driver with this name exists
   */
  async requireDriver(name: string): Promise<string> {
    const report = await this.getReport();
    return report.requireByName(name).uuid;
  }

  /**
   * Look up an optional driver by name, returning its UUID or undefined.
   *
   * @param name - The driver name (`jumpstarter.dev/name` label value)
   * @returns The driver instance UUID, or undefined if not found
   */
  async optionalDriver(name: string): Promise<string | undefined> {
    const report = await this.getReport();
    return report.findByName(name)?.uuid;
  }

  /**
   * Create a gRPC interceptor that routes calls to a specific driver instance.
   *
   * @param uuid - The driver instance UUID
   * @returns A gRPC interceptor function
   */
  createUuidInterceptor(uuid: string) {
    return createUuidInterceptor(uuid);
  }

  /** Close the session and release the gRPC channel. */
  close(): void {
    this.channel.close();
  }

}

function isTcpAddress(host: string): boolean {
  if (host.startsWith("/")) return false;
  const colon = host.lastIndexOf(":");
  if (colon <= 0) return false;
  const port = parseInt(host.substring(colon + 1), 10);
  return !isNaN(port) && port > 0 && port < 65536;
}

/**
 * Minimal protobuf decoder for GetReportResponse.
 *
 * Wire format (proto3):
 *   field 1 (string): uuid
 *   field 2 (map<string,string>): labels
 *   field 3 (repeated DriverInstanceReport): reports
 *   field 4 (repeated Endpoint): alternative_endpoints
 *
 * We decode just enough to build the DriverReport.
 */
function decodeGetReportResponse(buffer: Buffer): {
  uuid: string;
  labelsMap: Array<[string, string]>;
  reportsList: Array<{
    uuid: string;
    parentUuid?: string;
    labelsMap: Array<[string, string]>;
    description?: string;
    methodsDescriptionMap: Array<[string, string]>;
    nativeServicesList: string[];
  }>;
} {
  const reader = new ProtobufReader(buffer);
  let uuid = "";
  const labelsMap: Array<[string, string]> = [];
  const reportsList: Array<any> = [];

  while (reader.hasMore()) {
    const [fieldNumber, wireType] = reader.readTag();
    switch (fieldNumber) {
      case 1: // uuid (string)
        uuid = reader.readString();
        break;
      case 2: // labels (map entry — length-delimited submessage)
        {
          const entry = reader.readBytes();
          const [key, value] = decodeMapEntry(entry);
          labelsMap.push([key, value]);
        }
        break;
      case 3: // reports (repeated DriverInstanceReport)
        {
          const reportBytes = reader.readBytes();
          reportsList.push(decodeDriverInstanceReport(reportBytes));
        }
        break;
      default:
        reader.skipField(wireType);
        break;
    }
  }

  return { uuid, labelsMap, reportsList };
}

function decodeDriverInstanceReport(buffer: Buffer): {
  uuid: string;
  parentUuid?: string;
  labelsMap: Array<[string, string]>;
  description?: string;
  methodsDescriptionMap: Array<[string, string]>;
  nativeServicesList: string[];
} {
  const reader = new ProtobufReader(buffer);
  let uuid = "";
  let parentUuid: string | undefined;
  const labelsMap: Array<[string, string]> = [];
  let description: string | undefined;
  const methodsDescriptionMap: Array<[string, string]> = [];
  const nativeServicesList: string[] = [];

  while (reader.hasMore()) {
    const [fieldNumber, wireType] = reader.readTag();
    switch (fieldNumber) {
      case 1:
        uuid = reader.readString();
        break;
      case 2:
        parentUuid = reader.readString();
        break;
      case 3: {
        const entry = reader.readBytes();
        const [key, value] = decodeMapEntry(entry);
        labelsMap.push([key, value]);
        break;
      }
      case 4:
        description = reader.readString();
        break;
      case 5: {
        const entry = reader.readBytes();
        const [key, value] = decodeMapEntry(entry);
        methodsDescriptionMap.push([key, value]);
        break;
      }
      case 7:
        nativeServicesList.push(reader.readString());
        break;
      default:
        reader.skipField(wireType);
        break;
    }
  }

  return { uuid, parentUuid, labelsMap, description, methodsDescriptionMap, nativeServicesList };
}

function decodeMapEntry(buffer: Buffer): [string, string] {
  const reader = new ProtobufReader(buffer);
  let key = "";
  let value = "";
  while (reader.hasMore()) {
    const [fieldNumber, wireType] = reader.readTag();
    switch (fieldNumber) {
      case 1:
        key = reader.readString();
        break;
      case 2:
        value = reader.readString();
        break;
      default:
        reader.skipField(wireType);
        break;
    }
  }
  return [key, value];
}

/** Minimal protobuf wire format reader. */
class ProtobufReader {
  private offset = 0;

  constructor(private readonly buffer: Buffer) {}

  hasMore(): boolean {
    return this.offset < this.buffer.length;
  }

  readTag(): [number, number] {
    const varint = this.readVarint();
    const fieldNumber = varint >>> 3;
    const wireType = varint & 0x07;
    return [fieldNumber, wireType];
  }

  readVarint(): number {
    let result = 0;
    let shift = 0;
    while (this.offset < this.buffer.length) {
      const byte = this.buffer[this.offset++];
      result |= (byte & 0x7f) << shift;
      if ((byte & 0x80) === 0) return result;
      shift += 7;
    }
    return result;
  }

  readString(): string {
    const length = this.readVarint();
    const str = this.buffer.toString("utf8", this.offset, this.offset + length);
    this.offset += length;
    return str;
  }

  readBytes(): Buffer {
    const length = this.readVarint();
    const bytes = this.buffer.subarray(this.offset, this.offset + length);
    this.offset += length;
    return bytes;
  }

  skipField(wireType: number): void {
    switch (wireType) {
      case 0: // varint
        this.readVarint();
        break;
      case 1: // 64-bit
        this.offset += 8;
        break;
      case 2: // length-delimited
        {
          const len = this.readVarint();
          this.offset += len;
        }
        break;
      case 5: // 32-bit
        this.offset += 4;
        break;
    }
  }
}
