/**
 * Creates a local UDP socket that forwards datagrams over a gRPC
 * `RouterService.Stream` to a remote driver method.
 *
 * Each incoming datagram maps to one StreamChannel message, preserving
 * message boundaries.
 */

import * as dgram from "dgram";
import type { ExporterSession } from "./ExporterSession";
import { openDriverStream } from "./streamProto";
import type { StreamChannel } from "./StreamChannel";

export class UdpPortforwardAdapter {
  private readonly socket: dgram.Socket;
  private readonly _address: string;
  private readonly _port: number;
  private channel: StreamChannel | null = null;
  private readLoop: Promise<void> | null = null;

  private constructor(socket: dgram.Socket, address: string, port: number) {
    this.socket = socket;
    this._address = address;
    this._port = port;
  }

  /** The local address the UDP socket is bound to. */
  get address(): string {
    return this._address;
  }

  /** The local port the UDP socket is bound to. */
  get port(): number {
    return this._port;
  }

  /**
   * Open a UDP port-forward adapter that listens on an ephemeral local port.
   *
   * Opens a single `RouterService.Stream` and forwards datagrams bidirectionally.
   * Each UDP datagram sent to the local port becomes one gRPC stream message,
   * and each incoming stream message is sent back as a datagram to the last
   * known remote address.
   *
   * @param session - The exporter session providing address and credentials
   * @param driverUuid - UUID of the target driver instance
   * @param method - The driver method to invoke (e.g. "connect")
   * @returns A started UdpPortforwardAdapter
   */
  static async open(
    session: ExporterSession,
    driverUuid: string,
    method: string,
  ): Promise<UdpPortforwardAdapter> {
    const socket = dgram.createSocket("udp4");

    const host = "127.0.0.1";
    await new Promise<void>((resolve, reject) => {
      socket.bind(0, host, () => resolve());
      socket.once("error", reject);
    });

    const addr = socket.address();
    const adapter = new UdpPortforwardAdapter(socket, addr.address, addr.port);

    const { channel } = openDriverStream(session, driverUuid, method);
    adapter.channel = channel;

    // Track the last peer so we can send responses back
    let lastRemotePort = 0;
    let lastRemoteAddress = "";

    // Forward incoming UDP datagrams → gRPC stream
    socket.on("message", (msg: Buffer, rinfo: dgram.RemoteInfo) => {
      lastRemotePort = rinfo.port;
      lastRemoteAddress = rinfo.address;
      channel.write(msg).catch(() => {
        // stream closed, ignore
      });
    });

    // Forward gRPC stream → UDP datagrams back to last known peer
    adapter.readLoop = (async () => {
      try {
        for await (const chunk of channel) {
          if (lastRemotePort > 0) {
            socket.send(chunk, lastRemotePort, lastRemoteAddress);
          }
        }
      } catch {
        // stream ended or errored
      }
    })();

    return adapter;
  }

  /** Close the UDP socket and the gRPC stream. */
  close(): void {
    this.channel?.close();
    this.channel = null;
    try {
      this.socket.close();
    } catch {
      // already closed
    }
  }
}
