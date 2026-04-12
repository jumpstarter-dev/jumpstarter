package dev.jumpstarter.client;

import com.google.protobuf.Empty;
import io.grpc.Channel;
import io.grpc.Grpc;
import io.grpc.InsecureChannelCredentials;
import io.grpc.ManagedChannel;
import io.grpc.StatusRuntimeException;
import jumpstarter.v1.ExporterServiceGrpc;
import jumpstarter.v1.Jumpstarter;
import org.jetbrains.annotations.NotNull;

import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.InetSocketAddress;
import java.net.ServerSocket;
import java.net.Socket;
import java.net.UnixDomainSocketAddress;
import java.nio.ByteBuffer;
import java.nio.channels.Channels;
import java.nio.channels.SocketChannel;
import java.util.concurrent.TimeUnit;
import java.util.logging.Level;
import java.util.logging.Logger;

/**
 * A session connected to a Jumpstarter exporter inside a {@code jmp shell}.
 *
 * <p>Usage:
 * <pre>{@code
 * try (ExporterSession session = ExporterSession.fromEnv()) {
 *     DriverClient power = session.driverClientByName("power");
 *     power.call("on");
 * }
 * }</pre>
 */
public final class ExporterSession implements AutoCloseable {

    private static final Logger logger = Logger.getLogger(ExporterSession.class.getName());
    private static final String ENV_HOST = "JUMPSTARTER_HOST";

    private final ManagedChannel exporterChannel;
    private final ExporterServiceGrpc.ExporterServiceBlockingStub exporterStub;
    private DriverReport cachedReport;

    ExporterSession(@NotNull ManagedChannel channel) {
        this.exporterChannel = channel;
        this.exporterStub = ExporterServiceGrpc.newBlockingStub(channel);
    }

    /**
     * Connect to an exporter using the {@code JUMPSTARTER_HOST} environment variable
     * set by {@code jmp shell}.
     *
     * <p>Supports both Unix domain sockets (default for {@code jmp shell}) and TCP
     * addresses (used with {@code jmp shell --tls-grpc host:port}).
     *
     * @return an exporter session connected via the shell's socket
     * @throws IllegalStateException if JUMPSTARTER_HOST is not set
     */
    @NotNull
    public static ExporterSession fromEnv() {
        String host = System.getenv(ENV_HOST);
        if (host == null || host.isEmpty()) {
            throw new IllegalStateException(
                    "JUMPSTARTER_HOST environment variable is not set. "
                            + "Are you running inside a 'jmp shell' session?");
        }
        ManagedChannel channel = isTcpAddress(host)
                ? createTcpChannel(host)
                : createUnixChannel(host);
        return new ExporterSession(channel);
    }

    /**
     * Get the device report from the exporter.
     *
     * <p>The report describes the driver instance tree: each driver's UUID, labels,
     * description, and available methods. Results are cached after the first call.
     *
     * @return the driver report
     * @throws StatusRuntimeException if the RPC fails
     */
    @NotNull
    public DriverReport getReport() {
        if (cachedReport == null) {
            Jumpstarter.GetReportResponse response = exporterStub.getReport(Empty.getDefaultInstance());
            cachedReport = new DriverReport(response);
        }
        return cachedReport;
    }

    /**
     * Get the underlying gRPC channel for creating native gRPC stubs.
     *
     * <p>Used by generated typed clients to create per-driver native gRPC stubs
     * with a {@link UuidMetadataInterceptor} for driver instance routing.
     *
     * @return the gRPC channel to the exporter
     */
    @NotNull
    public Channel getChannel() {
        return exporterChannel;
    }

    /**
     * Check whether a driver with the given name exists in the device tree.
     *
     * @param name the driver name (value of the {@code jumpstarter.dev/name} label)
     * @return true if a driver with this name exists
     */
    public boolean hasDriver(@NotNull String name) {
        return getReport().findByName(name) != null;
    }

    /**
     * Create a driver client for the driver instance with the given UUID.
     *
     * @param uuid the driver instance UUID
     * @return a client for invoking methods on this driver
     * @deprecated Use native gRPC stubs with {@link UuidMetadataInterceptor} instead.
     */
    @Deprecated
    @NotNull
    public DriverClient driverClient(@NotNull String uuid) {
        return new DriverClient(uuid, exporterStub);
    }

    /**
     * Create a driver client by looking up a driver by its {@code jumpstarter.dev/name} label.
     *
     * @param name the driver name (value of the {@code jumpstarter.dev/name} label)
     * @return a client for invoking methods on this driver
     * @throws IllegalArgumentException if no driver with this name exists
     * @deprecated Use generated typed clients instead.
     */
    @Deprecated
    @NotNull
    public DriverClient driverClientByName(@NotNull String name) {
        DriverReport report = getReport();
        DriverReport.DriverInstance instance = report.findByName(name);
        if (instance == null) {
            throw new IllegalArgumentException("No driver found with name: " + name
                    + ". Available: " + report.getInstances());
        }
        return driverClient(instance.getUuid());
    }

    /**
     * End the current session and trigger the afterLease hook on the exporter.
     *
     * @return true if the session was ended successfully
     */
    public boolean endSession() {
        try {
            Jumpstarter.EndSessionResponse response = exporterStub.endSession(
                    Jumpstarter.EndSessionRequest.getDefaultInstance());
            return response.getSuccess();
        } catch (StatusRuntimeException e) {
            logger.log(Level.FINE, "EndSession error (may be expected)", e);
            return false;
        }
    }

    @Override
    public void close() {
        exporterChannel.shutdownNow();
        try {
            exporterChannel.awaitTermination(5, TimeUnit.SECONDS);
        } catch (InterruptedException ignored) {
            Thread.currentThread().interrupt();
        }
    }

    private static boolean isTcpAddress(String host) {
        if (host.startsWith("/")) return false;
        int colon = host.lastIndexOf(':');
        if (colon <= 0) return false;
        try {
            Integer.parseInt(host.substring(colon + 1));
            return true;
        } catch (NumberFormatException e) {
            return false;
        }
    }

    private static ManagedChannel createUnixChannel(String socketPath) {
        logger.info("Connecting to exporter via Unix socket: " + socketPath);
        try {
            ServerSocket proxyServer = new ServerSocket();
            proxyServer.setReuseAddress(true);
            proxyServer.bind(new InetSocketAddress("127.0.0.1", 0));
            int localPort = proxyServer.getLocalPort();
            Thread proxyThread = new Thread(() -> {
                try {
                    while (!proxyServer.isClosed()) {
                        Socket tcpClient = proxyServer.accept();
                        SocketChannel unixChannel = SocketChannel.open(
                                UnixDomainSocketAddress.of(socketPath));
                        bridgeStreams(tcpClient, unixChannel);
                    }
                } catch (IOException e) {
                    if (!proxyServer.isClosed()) {
                        logger.log(Level.WARNING, "Unix proxy accept error", e);
                    }
                }
            }, "jumpstarter-unix-proxy");
            proxyThread.setDaemon(true);
            proxyThread.start();
            return Grpc.newChannelBuilderForAddress(
                    "127.0.0.1", localPort, InsecureChannelCredentials.create()).build();
        } catch (IOException e) {
            throw new RuntimeException("Failed to create Unix socket proxy", e);
        }
    }

    private static void bridgeStreams(Socket tcpClient, SocketChannel unixChannel) {
        Thread t2u = new Thread(() -> {
            try {
                InputStream tcpIn = tcpClient.getInputStream();
                byte[] buf = new byte[32768];
                int n;
                while ((n = tcpIn.read(buf)) != -1) {
                    unixChannel.write(ByteBuffer.wrap(buf, 0, n));
                }
            } catch (IOException ignored) {
            } finally {
                try { unixChannel.close(); } catch (IOException ignored) {}
                try { tcpClient.close(); } catch (IOException ignored) {}
            }
        }, "jumpstarter-unix-t2u");
        t2u.setDaemon(true);
        t2u.start();
        Thread u2t = new Thread(() -> {
            try {
                OutputStream tcpOut = tcpClient.getOutputStream();
                InputStream unixIn = Channels.newInputStream(unixChannel);
                byte[] buf = new byte[32768];
                int n;
                while ((n = unixIn.read(buf)) != -1) {
                    tcpOut.write(buf, 0, n);
                    tcpOut.flush();
                }
            } catch (IOException ignored) {
            } finally {
                try { unixChannel.close(); } catch (IOException ignored) {}
                try { tcpClient.close(); } catch (IOException ignored) {}
            }
        }, "jumpstarter-unix-u2t");
        u2t.setDaemon(true);
        u2t.start();
    }

    private static ManagedChannel createTcpChannel(String host) {
        logger.info("Connecting to exporter via TCP: " + host);
        String hostname = host.substring(0, host.lastIndexOf(':'));
        int port = Integer.parseInt(host.substring(host.lastIndexOf(':') + 1));
        return Grpc.newChannelBuilderForAddress(
                hostname, port, InsecureChannelCredentials.create()).build();
    }
}
