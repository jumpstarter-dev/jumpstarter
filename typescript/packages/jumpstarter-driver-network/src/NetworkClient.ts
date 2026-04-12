/**
 * Network driver client that provides typed gRPC access to the
 * `NetworkInterface` service and TCP/UDP port forwarding for the
 * `Connect` bidi stream method.
 *
 * The `Connect` RPC uses native gRPC bidirectional streaming directly
 * against the `NetworkInterface` service definition.
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
  createUuidInterceptor,
} from "@jumpstarter/client";
import * as grpc from "@grpc/grpc-js";
import * as protoLoader from "@grpc/proto-loader";
import * as path from "path";
import * as net from "net";
import * as dgram from "dgram";

const CONNECT_PATH = "/jumpstarter.interfaces.network.v1.NetworkInterface/Connect";

export class NetworkClient {
  private readonly client: grpc.Client;
  private readonly interceptor: ReturnType<typeof createUuidInterceptor>;
  private readonly svcDef: any;
  private readonly session: ExporterSession;
  private readonly driverUuid: string;

  private tcpServer: net.Server | null = null;
  private tcpConnections: Set<net.Socket> = new Set();
  private udpSocket: dgram.Socket | null = null;
  private udpBidiStream: grpc.ClientDuplexStream<any, any> | null = null;

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
   * Open a native gRPC bidi stream for the `Connect` RPC.
   */
  private _openConnectStream(): grpc.ClientDuplexStream<any, any> {
    const connectDef = this.svcDef["Connect"];
    return this.client.makeBidiStreamRequest(
      CONNECT_PATH,
      connectDef.requestSerialize,
      connectDef.responseDeserialize,
      new grpc.Metadata(),
      { interceptors: [this.interceptor] },
    );
  }

  /**
   * Start a local TCP listener that forwards connections to the remote
   * device's network endpoint via the `Connect` bidi stream.
   *
   * Each incoming TCP connection opens a new bidi stream and bridges
   * bytes bidirectionally.
   *
   * @returns The local address and port to connect to
   */
  async connectTcp(): Promise<{ address: string; port: number }> {
    const server = net.createServer((socket) => {
      this.tcpConnections.add(socket);

      const bidiStream = this._openConnectStream();

      // Forward socket → gRPC stream
      socket.on("data", (chunk: Buffer) => {
        bidiStream.write({ payload: chunk });
      });
      socket.on("end", () => {
        bidiStream.end();
      });
      socket.on("error", () => {
        bidiStream.end();
      });

      // Forward gRPC stream → socket
      bidiStream.on("data", (msg: any) => {
        if (!socket.destroyed) {
          socket.write(msg.payload);
        }
      });
      bidiStream.on("end", () => {
        if (!socket.destroyed) {
          socket.end();
        }
      });
      bidiStream.on("error", () => {
        socket.destroy();
      });

      socket.on("close", () => {
        this.tcpConnections.delete(socket);
      });
    });

    const host = "127.0.0.1";
    await new Promise<void>((resolve, reject) => {
      server.listen(0, host, () => resolve());
      server.once("error", reject);
    });

    this.tcpServer = server;
    const addr = server.address() as net.AddressInfo;
    return { address: addr.address, port: addr.port };
  }

  /**
   * Start a local UDP socket that forwards datagrams to the remote
   * device's network endpoint via the `Connect` bidi stream.
   *
   * Opens a single bidi stream and forwards datagrams bidirectionally.
   *
   * @returns The local address and port to send datagrams to
   */
  async connectUdp(): Promise<{ address: string; port: number }> {
    const socket = dgram.createSocket("udp4");

    const host = "127.0.0.1";
    await new Promise<void>((resolve, reject) => {
      socket.bind(0, host, () => resolve());
      socket.once("error", reject);
    });

    const bidiStream = this._openConnectStream();
    this.udpSocket = socket;
    this.udpBidiStream = bidiStream;

    let lastRemotePort = 0;
    let lastRemoteAddress = "";

    // Forward incoming UDP datagrams → gRPC stream
    socket.on("message", (msg: Buffer, rinfo: dgram.RemoteInfo) => {
      lastRemotePort = rinfo.port;
      lastRemoteAddress = rinfo.address;
      bidiStream.write({ payload: msg });
    });

    // Forward gRPC stream → UDP datagrams back to last known peer
    bidiStream.on("data", (msg: any) => {
      if (lastRemotePort > 0) {
        socket.send(msg.payload, lastRemotePort, lastRemoteAddress);
      }
    });
    bidiStream.on("error", () => {
      // stream ended or errored
    });

    const addr = socket.address();
    return { address: addr.address, port: addr.port };
  }

  /**
   * Alias for `connectTcp()` for backwards compatibility.
   */
  async connect(): Promise<{ address: string; port: number }> {
    return this.connectTcp();
  }

  /** Stop all port-forward listeners and close the gRPC client. */
  close(): void {
    if (this.tcpServer) {
      this.tcpServer.close();
      for (const socket of this.tcpConnections) {
        socket.destroy();
      }
      this.tcpConnections.clear();
      this.tcpServer = null;
    }
    if (this.udpBidiStream) {
      this.udpBidiStream.end();
      this.udpBidiStream = null;
    }
    if (this.udpSocket) {
      try {
        this.udpSocket.close();
      } catch {
        // already closed
      }
      this.udpSocket = null;
    }
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
