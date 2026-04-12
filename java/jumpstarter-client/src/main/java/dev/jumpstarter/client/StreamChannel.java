package dev.jumpstarter.client;

import io.grpc.stub.StreamObserver;
import jumpstarter.v1.Router;
import jumpstarter.v1.RouterServiceGrpc;
import org.jetbrains.annotations.NotNull;

import java.io.*;
import java.util.concurrent.BlockingQueue;
import java.util.concurrent.LinkedBlockingQueue;

/**
 * Wraps a {@code RouterService.Stream} bidirectional stream as a pair of
 * {@link InputStream} and {@link OutputStream} for {@code @exportstream} methods.
 *
 * <p>Usage:
 * <pre>{@code
 * StreamChannel ch = StreamChannel.open(routerStub);
 * ch.outputStream().write("AT\r\n".getBytes());
 * int n = ch.inputStream().read(buf);
 * ch.close();
 * }</pre>
 */
public final class StreamChannel implements AutoCloseable {

    private static final byte[] SENTINEL = new byte[0];

    private final StreamObserver<Router.StreamRequest> requestObserver;
    private final BlockingQueue<byte[]> incomingQueue = new LinkedBlockingQueue<>();
    private final StreamChannelInputStream inputStream = new StreamChannelInputStream();
    private final StreamChannelOutputStream outputStream = new StreamChannelOutputStream();
    private volatile boolean closed = false;

    private StreamChannel(@NotNull StreamObserver<Router.StreamRequest> requestObserver) {
        this.requestObserver = requestObserver;
    }

    /**
     * Open a new stream channel using the given router stub.
     *
     * @param stub the RouterService async stub
     * @return an open stream channel
     */
    @NotNull
    public static StreamChannel open(@NotNull RouterServiceGrpc.RouterServiceStub stub) {
        StreamChannel channel = new StreamChannel(null);
        StreamObserver<Router.StreamRequest> reqObserver = stub.stream(
                new StreamObserver<>() {
                    @Override
                    public void onNext(Router.StreamResponse value) {
                        if (value.getFrameType() == Router.FrameType.FRAME_TYPE_DATA) {
                            channel.incomingQueue.offer(value.getPayload().toByteArray());
                        }
                    }

                    @Override
                    public void onError(Throwable t) {
                        channel.closed = true;
                        channel.incomingQueue.offer(SENTINEL);
                    }

                    @Override
                    public void onCompleted() {
                        channel.closed = true;
                        channel.incomingQueue.offer(SENTINEL);
                    }
                });
        return new StreamChannel(reqObserver);
    }

    /** Get the input stream for reading data from the remote side. */
    @NotNull
    public InputStream inputStream() {
        return inputStream;
    }

    /** Get the output stream for writing data to the remote side. */
    @NotNull
    public OutputStream outputStream() {
        return outputStream;
    }

    @Override
    public void close() {
        if (!closed) {
            closed = true;
            try {
                requestObserver.onCompleted();
            } catch (Exception ignored) {
            }
            incomingQueue.offer(SENTINEL);
        }
    }

    private class StreamChannelInputStream extends InputStream {
        private byte[] currentBuffer;
        private int currentOffset;

        @Override
        public int read() throws IOException {
            byte[] buf = new byte[1];
            int n = read(buf, 0, 1);
            return n == -1 ? -1 : buf[0] & 0xFF;
        }

        @Override
        public int read(byte @NotNull [] b, int off, int len) throws IOException {
            while (true) {
                if (currentBuffer != null && currentOffset < currentBuffer.length) {
                    int available = currentBuffer.length - currentOffset;
                    int toCopy = Math.min(available, len);
                    System.arraycopy(currentBuffer, currentOffset, b, off, toCopy);
                    currentOffset += toCopy;
                    if (currentOffset >= currentBuffer.length) {
                        currentBuffer = null;
                    }
                    return toCopy;
                }

                if (closed && incomingQueue.isEmpty()) {
                    return -1;
                }

                try {
                    byte[] data = incomingQueue.take();
                    if (data == SENTINEL) {
                        return -1;
                    }
                    currentBuffer = data;
                    currentOffset = 0;
                } catch (InterruptedException e) {
                    Thread.currentThread().interrupt();
                    throw new IOException("Interrupted while reading", e);
                }
            }
        }
    }

    private class StreamChannelOutputStream extends OutputStream {
        @Override
        public void write(int b) throws IOException {
            write(new byte[]{(byte) b}, 0, 1);
        }

        @Override
        public void write(byte @NotNull [] b, int off, int len) throws IOException {
            if (closed) {
                throw new IOException("Stream channel is closed");
            }
            Router.StreamRequest request = Router.StreamRequest.newBuilder()
                    .setPayload(com.google.protobuf.ByteString.copyFrom(b, off, len))
                    .setFrameType(Router.FrameType.FRAME_TYPE_DATA)
                    .build();
            try {
                requestObserver.onNext(request);
            } catch (Exception e) {
                throw new IOException("Failed to write to stream channel", e);
            }
        }

        @Override
        public void flush() {
            // gRPC handles its own buffering
        }
    }
}
