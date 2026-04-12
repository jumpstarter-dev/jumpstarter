/**
 * Network driver client that provides typed gRPC access to the
 * `NetworkInterface` service and TCP/UDP port forwarding for the
 * `Connect` bidi stream method.
 *
 * The `Connect` RPC is an `@exportstream` method — it goes through
 * `RouterService.Stream` (port forwarding), not through the native
 * gRPC stub directly.
 *
 * Usage:
 * ```typescript
 * const session = ExporterSession.fromEnv();
 * const uuid = await session.requireDriver("network");
 * const client = new NetworkClient(session, uuid);
 *
 * const tcp = await client.connectTcp();
 * // Connect to tcp.address:tcp.port via TCP
 *
 * const udp = await client.connectUdp();
 * // Send datagrams to udp.address:udp.port via UDP
 *
 * client.close();
 * ```
 */

import {
  ExporterSession,
  TcpPortforwardAdapter,
  UdpPortforwardAdapter,
  createUuidInterceptor,
} from "@jumpstarter/client";
import * as grpc from "@grpc/grpc-js";
import * as protoLoader from "@grpc/proto-loader";
import * as path from "path";

export class NetworkClient {
  private readonly client: grpc.Client;
  private readonly interceptor: ReturnType<typeof createUuidInterceptor>;
  private readonly svcDef: any;
  private readonly session: ExporterSession;
  private readonly driverUuid: string;

  private tcpAdapter: TcpPortforwardAdapter | null = null;
  private udpAdapter: UdpPortforwardAdapter | null = null;

  private static _grpcPkg: grpc.GrpcObject | null = null;

  private static _loadProto(): grpc.GrpcObject {
    if (!NetworkClient._grpcPkg) {
      const pd = protoLoader.loadSync(
        path.join(__dirname, "../proto/network.proto"),
        { keepCase: true, longs: String, enums: String, defaults: true },
      );
      NetworkClient._grpcPkg = grpc.loadPackageDefinition(pd);
    }
    return NetworkClient._grpcPkg;
  }

  constructor(session: ExporterSession, driverUuid: string) {
    this.session = session;
    this.driverUuid = driverUuid;
    this.client = new grpc.Client(session.getAddress(), session.getCredentials(), {});
    this.interceptor = createUuidInterceptor(driverUuid);
    const pkg = NetworkClient._loadProto();
    const svc = (pkg as any)["jumpstarter"]["interfaces"]["network"]["v1"].NetworkInterface;
    this.svcDef = svc.service;
  }

  /**
   * Start a local TCP listener that forwards connections to the remote
   * device's network endpoint via the `Connect` bidi stream.
   *
   * @param method - The driver method to invoke (default: "connect")
   * @returns The local address and port to connect to
   */
  async connectTcp(
    method: string = "connect",
  ): Promise<{ address: string; port: number }> {
    this.tcpAdapter = await TcpPortforwardAdapter.open(
      this.session,
      this.driverUuid,
      method,
    );
    return { address: this.tcpAdapter.address, port: this.tcpAdapter.port };
  }

  /**
   * Start a local UDP socket that forwards datagrams to the remote
   * device's network endpoint via the `Connect` bidi stream.
   *
   * @param method - The driver method to invoke (default: "connect")
   * @returns The local address and port to send datagrams to
   */
  async connectUdp(
    method: string = "connect",
  ): Promise<{ address: string; port: number }> {
    this.udpAdapter = await UdpPortforwardAdapter.open(
      this.session,
      this.driverUuid,
      method,
    );
    return { address: this.udpAdapter.address, port: this.udpAdapter.port };
  }

  /**
   * Alias for `connectTcp("connect")` for backwards compatibility.
   */
  async connect(): Promise<{ address: string; port: number }> {
    return this.connectTcp("connect");
  }

  /** Stop all port-forward listeners and close the gRPC client. */
  close(): void {
    this.tcpAdapter?.close();
    this.tcpAdapter = null;
    this.udpAdapter?.close();
    this.udpAdapter = null;
    this.client.close();
  }

  // -- Native gRPC stub helpers for future non-streaming RPCs --

  /** @internal */
  protected _unary<T>(path: string, methodName: string, request: any): Promise<T> {
    const methodDef = this.svcDef[methodName];
    return new Promise<T>((resolve, reject) => {
      this.client.makeUnaryRequest(
        path,
        methodDef.requestSerialize,
        methodDef.responseDeserialize,
        request,
        new grpc.Metadata(),
        { interceptors: [this.interceptor] },
        (err: grpc.ServiceError | null, resp?: T) => {
          if (err) reject(err);
          else resolve(resp as T);
        },
      );
    });
  }

  /** @internal */
  protected async *_serverStream<T>(
    path: string,
    methodName: string,
    request: any,
  ): AsyncIterableIterator<T> {
    const methodDef = this.svcDef[methodName];
    const stream = this.client.makeServerStreamRequest(
      path,
      methodDef.requestSerialize,
      methodDef.responseDeserialize,
      request,
      new grpc.Metadata(),
      { interceptors: [this.interceptor] },
    );
    for await (const msg of stream) {
      yield msg;
    }
  }
}
