package dev.jumpstarter.client;

import jumpstarter.v1.RouterServiceGrpc;
import org.jetbrains.annotations.NotNull;

import java.io.IOException;
import java.io.InputStream;
import java.net.DatagramPacket;
import java.net.DatagramSocket;
import java.net.InetSocketAddress;
import java.net.SocketAddress;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;
import java.util.logging.Level;
import java.util.logging.Logger;

/**
 * Creates a local UDP listener that forwards datagrams over a
 * {@link StreamChannel} to a remote driver's stream method.
 *
 * <p>Each unique remote address (client) gets its own {@link StreamChannel}.
 * Each datagram maps to one StreamChannel message.
 *
 * <p>Usage:
 * <pre>{@code
 * try (UdpPortforwardAdapter adapter = UdpPortforwardAdapter.open(session, driverUuid, "connect")) {
 *     InetSocketAddress addr = adapter.getLocalAddress();
 *     // Send/receive UDP datagrams to addr
 * }
 * }</pre>
 */
public final class UdpPortforwardAdapter implements AutoCloseable {

    private static final Logger logger = Logger.getLogger(UdpPortforwardAdapter.class.getName());
    private static final int BUFFER_SIZE = 65535;

    private final DatagramSocket socket;
    private final Map<SocketAddress, StreamChannel> channels = new ConcurrentHashMap<>();
    private volatile boolean closed = false;

    private UdpPortforwardAdapter(@NotNull DatagramSocket socket) {
        this.socket = socket;
    }

    /**
     * Open a UDP port-forward adapter that listens on an ephemeral local port.
     *
     * <p>Incoming datagrams are forwarded to the specified driver stream method.
     * Responses from the stream are sent back as datagrams to the original sender.
     *
     * @param session    the exporter session providing the gRPC channel
     * @param driverUuid the target driver instance UUID
     * @param method     the stream method name (e.g. "connect")
     * @return a running adapter with an active local UDP listener
     */
    @NotNull
    public static UdpPortforwardAdapter open(
            @NotNull ExporterSession session,
            @NotNull String driverUuid,
            @NotNull String method) {
        try {
            DatagramSocket ds = new DatagramSocket(new InetSocketAddress("127.0.0.1", 0));
            UdpPortforwardAdapter adapter = new UdpPortforwardAdapter(ds);

            Thread recvThread = new Thread(() -> adapter.receiveLoop(session, driverUuid, method),
                    "jumpstarter-udpfwd-recv-" + ds.getLocalPort());
            recvThread.setDaemon(true);
            recvThread.start();

            logger.info("UDP port-forward listening on " + ds.getLocalSocketAddress()
                    + " → driver " + driverUuid + "." + method);
            return adapter;
        } catch (IOException e) {
            throw new RuntimeException("Failed to create UDP port-forward listener", e);
        }
    }

    /**
     * Get the local address and port the listener is bound to.
     *
     * @return the local socket address
     */
    @NotNull
    public InetSocketAddress getLocalAddress() {
        return (InetSocketAddress) socket.getLocalSocketAddress();
    }

    @Override
    public void close() {
        if (closed) return;
        closed = true;
        socket.close();
        for (StreamChannel ch : channels.values()) {
            try { ch.close(); } catch (Exception ignored) {}
        }
        channels.clear();
    }

    private void receiveLoop(ExporterSession session, String driverUuid, String method) {
        RouterServiceGrpc.RouterServiceStub routerStub =
                RouterServiceGrpc.newStub(session.getChannel());
        byte[] buf = new byte[BUFFER_SIZE];

        while (!closed) {
            try {
                DatagramPacket packet = new DatagramPacket(buf, buf.length);
                socket.receive(packet);

                SocketAddress sender = packet.getSocketAddress();
                StreamChannel channel = channels.computeIfAbsent(sender, addr -> {
                    StreamChannel ch = StreamChannel.open(routerStub, driverUuid, method);
                    startReturnThread(addr, ch);
                    return ch;
                });

                byte[] data = new byte[packet.getLength()];
                System.arraycopy(packet.getData(), packet.getOffset(), data, 0, packet.getLength());
                channel.outputStream().write(data);
            } catch (IOException e) {
                if (!closed) {
                    logger.log(Level.WARNING, "UDP port-forward receive error", e);
                }
            }
        }
    }

    private void startReturnThread(SocketAddress sender, StreamChannel channel) {
        Thread t = new Thread(() -> {
            try {
                InputStream in = channel.inputStream();
                byte[] buf = new byte[BUFFER_SIZE];
                int n;
                while ((n = in.read(buf)) != -1) {
                    DatagramPacket reply = new DatagramPacket(buf, 0, n, sender);
                    socket.send(reply);
                }
            } catch (IOException e) {
                if (!closed) {
                    logger.log(Level.FINE, "UDP return stream ended", e);
                }
            } finally {
                channels.remove(sender);
                try { channel.close(); } catch (Exception ignored) {}
            }
        }, "jumpstarter-udpfwd-ret-" + sender);
        t.setDaemon(true);
        t.start();
    }
}
