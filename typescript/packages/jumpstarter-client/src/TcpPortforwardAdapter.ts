/**
 * Creates a local TCP listener that forwards each incoming connection
 * over a gRPC `RouterService.Stream` to a remote driver method.
 *
 * This is the TypeScript equivalent of the Python `TcpPortforwardAdapter`.
 */

import * as net from "net";
import type { ExporterSession } from "./ExporterSession";
import { openDriverStream } from "./streamProto";

export class TcpPortforwardAdapter {
  private readonly server: net.Server;
  private readonly connections: Set<net.Socket> = new Set();
  private readonly _address: string;
  private readonly _port: number;

  private constructor(server: net.Server, address: string, port: number) {
    this.server = server;
    this._address = address;
    this._port = port;
  }

  /** The local address the TCP listener is bound to. */
  get address(): string {
    return this._address;
  }

  /** The local port the TCP listener is bound to. */
  get port(): number {
    return this._port;
  }

  /**
   * Open a TCP port-forward adapter that listens on an ephemeral local port.
   *
   * Each incoming TCP connection opens a new `RouterService.Stream` and
   * bidirectionally forwards bytes between the socket and the gRPC stream.
   *
   * @param session - The exporter session providing address and credentials
   * @param driverUuid - UUID of the target driver instance
   * @param method - The driver method to invoke (e.g. "connect")
   * @returns A started TcpPortforwardAdapter
   */
  static async open(
    session: ExporterSession,
    driverUuid: string,
    method: string,
  ): Promise<TcpPortforwardAdapter> {
    const server = net.createServer((socket) => {
      adapter.connections.add(socket);
      handleConnection(session, driverUuid, method, socket).finally(() => {
        adapter.connections.delete(socket);
      });
    });

    const host = "127.0.0.1";
    await new Promise<void>((resolve, reject) => {
      server.listen(0, host, () => resolve());
      server.once("error", reject);
    });

    const addr = server.address() as net.AddressInfo;
    const adapter = new TcpPortforwardAdapter(server, addr.address, addr.port);
    return adapter;
  }

  /** Stop the TCP listener and close all active connections. */
  close(): void {
    this.server.close();
    for (const socket of this.connections) {
      socket.destroy();
    }
    this.connections.clear();
  }
}

/**
 * Handle a single incoming TCP connection by opening a gRPC stream
 * and forwarding bytes in both directions.
 */
async function handleConnection(
  session: ExporterSession,
  driverUuid: string,
  method: string,
  socket: net.Socket,
): Promise<void> {
  const { channel } = openDriverStream(session, driverUuid, method);

  // Forward socket → gRPC stream
  const socketToStream = new Promise<void>((resolve) => {
    socket.on("data", (chunk: Buffer) => {
      channel.write(chunk).catch(() => {
        socket.destroy();
      });
    });
    socket.on("end", () => {
      channel.close();
      resolve();
    });
    socket.on("error", () => {
      channel.close();
      resolve();
    });
  });

  // Forward gRPC stream → socket
  const streamToSocket = (async () => {
    try {
      for await (const chunk of channel) {
        if (socket.destroyed) break;
        const ok = socket.write(chunk);
        if (!ok) {
          await new Promise<void>((resolve) => socket.once("drain", resolve));
        }
      }
    } catch {
      // stream ended or errored
    } finally {
      if (!socket.destroyed) {
        socket.end();
      }
    }
  })();

  await Promise.allSettled([socketToStream, streamToSocket]);
}
