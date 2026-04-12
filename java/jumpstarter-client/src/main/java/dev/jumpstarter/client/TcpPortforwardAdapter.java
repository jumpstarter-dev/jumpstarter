package dev.jumpstarter.client;

import jumpstarter.v1.RouterServiceGrpc;
import org.jetbrains.annotations.NotNull;

import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.InetSocketAddress;
import java.net.ServerSocket;
import java.net.Socket;
import java.util.concurrent.CopyOnWriteArrayList;
import java.util.logging.Level;
import java.util.logging.Logger;

/**
 * Creates a local TCP listener that forwards each accepted connection over a
 * {@link StreamChannel} to a remote driver's stream method.
 *
 * <p>This enables connecting local TCP clients (SSH, serial consoles, etc.)
 * to device-under-test services exposed by Jumpstarter drivers.
 *
 * <p>Usage:
 * <pre>{@code
 * try (TcpPortforwardAdapter adapter = TcpPortforwardAdapter.open(session, driverUuid, "connect")) {
 *     InetSocketAddress addr = adapter.getLocalAddress();
 *     // Connect to addr with any TCP client
 * }
 * }</pre>
 */
public final class TcpPortforwardAdapter implements AutoCloseable {

    private static final Logger logger = Logger.getLogger(TcpPortforwardAdapter.class.getName());
    private static final int BUFFER_SIZE = 32768;

    private final ServerSocket serverSocket;
    private final CopyOnWriteArrayList<Socket> activeSockets = new CopyOnWriteArrayList<>();
    private final CopyOnWriteArrayList<StreamChannel> activeChannels = new CopyOnWriteArrayList<>();
    private volatile boolean closed = false;

    private TcpPortforwardAdapter(@NotNull ServerSocket serverSocket) {
        this.serverSocket = serverSocket;
    }

    /**
     * Open a TCP port-forward adapter that listens on an ephemeral local port.
     *
     * <p>Each accepted TCP connection opens a new {@link StreamChannel} to the
     * specified driver stream method and bidirectionally bridges bytes.
     *
     * @param session    the exporter session providing the gRPC channel
     * @param driverUuid the target driver instance UUID
     * @param method     the stream method name (e.g. "connect")
     * @return a running adapter with an active local TCP listener
     */
    @NotNull
    public static TcpPortforwardAdapter open(
            @NotNull ExporterSession session,
            @NotNull String driverUuid,
            @NotNull String method) {
        try {
            ServerSocket ss = new ServerSocket();
            ss.setReuseAddress(true);
            ss.bind(new InetSocketAddress("127.0.0.1", 0));

            TcpPortforwardAdapter adapter = new TcpPortforwardAdapter(ss);

            Thread acceptThread = new Thread(() -> adapter.acceptLoop(session, driverUuid, method),
                    "jumpstarter-portfwd-accept-" + ss.getLocalPort());
            acceptThread.setDaemon(true);
            acceptThread.start();

            logger.info("TCP port-forward listening on " + ss.getLocalSocketAddress()
                    + " → driver " + driverUuid + "." + method);
            return adapter;
        } catch (IOException e) {
            throw new RuntimeException("Failed to create TCP port-forward listener", e);
        }
    }

    /**
     * Get the local address and port the listener is bound to.
     *
     * @return the local socket address
     */
    @NotNull
    public InetSocketAddress getLocalAddress() {
        return (InetSocketAddress) serverSocket.getLocalSocketAddress();
    }

    @Override
    public void close() {
        if (closed) return;
        closed = true;
        try {
            serverSocket.close();
        } catch (IOException ignored) {
        }
        for (Socket s : activeSockets) {
            try { s.close(); } catch (IOException ignored) {}
        }
        for (StreamChannel ch : activeChannels) {
            try { ch.close(); } catch (Exception ignored) {}
        }
    }

    private void acceptLoop(ExporterSession session, String driverUuid, String method) {
        RouterServiceGrpc.RouterServiceStub routerStub =
                RouterServiceGrpc.newStub(session.getChannel());

        while (!closed) {
            try {
                Socket client = serverSocket.accept();
                activeSockets.add(client);

                StreamChannel channel = StreamChannel.open(routerStub, driverUuid, method);
                activeChannels.add(channel);

                bridgeConnection(client, channel);
            } catch (IOException e) {
                if (!closed) {
                    logger.log(Level.WARNING, "Port-forward accept error", e);
                }
            }
        }
    }

    private void bridgeConnection(Socket client, StreamChannel channel) {
        // socket → stream
        Thread s2c = new Thread(() -> {
            try {
                InputStream in = client.getInputStream();
                OutputStream out = channel.outputStream();
                byte[] buf = new byte[BUFFER_SIZE];
                int n;
                while ((n = in.read(buf)) != -1) {
                    out.write(buf, 0, n);
                }
            } catch (IOException ignored) {
            } finally {
                cleanup(client, channel);
            }
        }, "jumpstarter-portfwd-s2c-" + client.getPort());
        s2c.setDaemon(true);
        s2c.start();

        // stream → socket
        Thread c2s = new Thread(() -> {
            try {
                InputStream in = channel.inputStream();
                OutputStream out = client.getOutputStream();
                byte[] buf = new byte[BUFFER_SIZE];
                int n;
                while ((n = in.read(buf)) != -1) {
                    out.write(buf, 0, n);
                    out.flush();
                }
            } catch (IOException ignored) {
            } finally {
                cleanup(client, channel);
            }
        }, "jumpstarter-portfwd-c2s-" + client.getPort());
        c2s.setDaemon(true);
        c2s.start();
    }

    private void cleanup(Socket client, StreamChannel channel) {
        try { channel.close(); } catch (Exception ignored) {}
        try { client.close(); } catch (IOException ignored) {}
        activeSockets.remove(client);
        activeChannels.remove(channel);
    }
}
