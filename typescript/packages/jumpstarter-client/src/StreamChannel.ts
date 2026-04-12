/**
 * Wraps a `RouterService.Stream` bidirectional gRPC stream as a Node.js
 * async iterable for `@exportstream` methods.
 *
 * Usage:
 * ```typescript
 * const channel = StreamChannel.open(routerClient);
 * await channel.write(Buffer.from("AT\r\n"));
 * for await (const chunk of channel) {
 *   console.log("Received:", chunk);
 * }
 * channel.close();
 * ```
 */

import type { ClientDuplexStream } from "@grpc/grpc-js";

/** Frame types matching the proto FrameType enum. */
const enum FrameType {
  DATA = 0x00,
  RST_STREAM = 0x03,
  PING = 0x06,
  GOAWAY = 0x07,
}

/**
 * Wraps a RouterService.Stream bidirectional gRPC stream, providing
 * write() and async iteration for reading.
 */
export class StreamChannel implements AsyncIterable<Uint8Array> {
  private closed = false;
  private readonly incoming: Array<Uint8Array | null> = [];
  private waitResolve: ((value: void) => void) | null = null;

  private constructor(
    private readonly stream: ClientDuplexStream<
      { payload: Uint8Array; frameType: number },
      { payload: Uint8Array; frameType: number }
    >,
  ) {
    stream.on("data", (msg: { payload: Uint8Array; frameType: number }) => {
      if (msg.frameType === FrameType.DATA) {
        this.incoming.push(msg.payload);
        this.waitResolve?.();
        this.waitResolve = null;
      }
    });

    stream.on("end", () => {
      this.closed = true;
      this.incoming.push(null); // sentinel
      this.waitResolve?.();
      this.waitResolve = null;
    });

    stream.on("error", () => {
      this.closed = true;
      this.incoming.push(null);
      this.waitResolve?.();
      this.waitResolve = null;
    });
  }

  /**
   * Open a stream channel using a RouterService client's `stream()` method.
   *
   * @param routerClient - A RouterService gRPC client with a `stream()` bidi method
   * @returns An open StreamChannel
   */
  static open(routerClient: { stream(): ClientDuplexStream<any, any> }): StreamChannel {
    const stream = routerClient.stream();
    return new StreamChannel(stream);
  }

  /**
   * Write data to the remote side.
   *
   * @param data - The data to send
   * @returns A promise that resolves when the write is flushed
   */
  async write(data: Uint8Array): Promise<void> {
    if (this.closed) {
      throw new Error("StreamChannel is closed");
    }
    return new Promise<void>((resolve, reject) => {
      this.stream.write(
        { payload: data, frameType: FrameType.DATA },
        (err: Error | null | undefined) => {
          if (err) reject(err);
          else resolve();
        },
      );
    });
  }

  /** Close the stream channel. */
  close(): void {
    if (!this.closed) {
      this.closed = true;
      this.stream.end();
    }
  }

  /** Async iterator for reading incoming data chunks. */
  async *[Symbol.asyncIterator](): AsyncIterableIterator<Uint8Array> {
    while (true) {
      if (this.incoming.length > 0) {
        const chunk = this.incoming.shift()!;
        if (chunk === null) return; // end of stream
        yield chunk;
      } else if (this.closed) {
        return;
      } else {
        await new Promise<void>((resolve) => {
          this.waitResolve = resolve;
        });
      }
    }
  }
}
