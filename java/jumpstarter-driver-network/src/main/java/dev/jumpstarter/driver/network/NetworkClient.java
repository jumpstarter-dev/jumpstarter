package dev.jumpstarter.driver.network;

import dev.jumpstarter.client.DriverReport;
import dev.jumpstarter.client.ExporterSession;
import dev.jumpstarter.client.TcpPortforwardAdapter;
import dev.jumpstarter.client.UdpPortforwardAdapter;
import dev.jumpstarter.client.UuidMetadataInterceptor;
import io.grpc.Channel;
import org.jetbrains.annotations.NotNull;
import org.jetbrains.annotations.Nullable;

import java.net.InetSocketAddress;

/**
 * Typed client for network drivers that provide TCP and UDP connectivity
 * to a device under test.
 *
 * <p>Combines a native gRPC stub for typed RPC methods with port-forward
 * adapters for {@code @exportstream} methods (like {@code Connect}).
 *
 * <p>Usage:
 * <pre>{@code
 * try (ExporterSession session = ExporterSession.fromEnv();
 *      NetworkClient network = new NetworkClient(session, "network")) {
 *     InetSocketAddress tcp = network.connectTcp();
 *     InetSocketAddress udp = network.connectUdp();
 * }
 * }</pre>
 */
public class NetworkClient implements AutoCloseable {

    private final ExporterSession session;
    private final String driverUuid;
    private final NetworkInterfaceGrpc.NetworkInterfaceBlockingStub blockingStub;
    private final NetworkInterfaceGrpc.NetworkInterfaceStub asyncStub;
    private @Nullable TcpPortforwardAdapter tcpAdapter;
    private @Nullable UdpPortforwardAdapter udpAdapter;

    /**
     * Create a network client for the named driver.
     *
     * @param session    the exporter session
     * @param driverName the driver name (value of the {@code jumpstarter.dev/name} label)
     * @throws IllegalArgumentException if no driver with this name exists
     */
    public NetworkClient(@NotNull ExporterSession session, @NotNull String driverName) {
        DriverReport.DriverInstance instance = session.getReport().findByName(driverName);
        if (instance == null) {
            throw new IllegalArgumentException("No driver found with name: " + driverName);
        }
        this.session = session;
        this.driverUuid = instance.getUuid();

        Channel channel = session.getChannel();
        UuidMetadataInterceptor interceptor = new UuidMetadataInterceptor(driverUuid);
        this.blockingStub = NetworkInterfaceGrpc.newBlockingStub(channel)
                .withInterceptors(interceptor);
        this.asyncStub = NetworkInterfaceGrpc.newStub(channel)
                .withInterceptors(interceptor);
    }

    /**
     * Get the native gRPC blocking stub for direct RPC calls.
     *
     * @return the blocking stub with UUID metadata interceptor
     */
    @NotNull
    public NetworkInterfaceGrpc.NetworkInterfaceBlockingStub getBlockingStub() {
        return blockingStub;
    }

    /**
     * Get the native gRPC async stub for direct RPC calls.
     *
     * @return the async stub with UUID metadata interceptor
     */
    @NotNull
    public NetworkInterfaceGrpc.NetworkInterfaceStub getAsyncStub() {
        return asyncStub;
    }

    /**
     * Alias for {@link #connectTcp()}.
     *
     * @return the local TCP address and port
     */
    @NotNull
    public InetSocketAddress connect() {
        return connectTcp();
    }

    /**
     * Open a local TCP listener forwarding to the default "connect" stream method.
     *
     * @return the local TCP address and port
     */
    @NotNull
    public InetSocketAddress connectTcp() {
        return connectTcp("connect");
    }

    /**
     * Open a local TCP listener forwarding to the specified stream method.
     *
     * @param method the stream method name
     * @return the local TCP address and port
     */
    @NotNull
    public InetSocketAddress connectTcp(@NotNull String method) {
        if (tcpAdapter != null) {
            tcpAdapter.close();
        }
        tcpAdapter = TcpPortforwardAdapter.open(session, driverUuid, method);
        return tcpAdapter.getLocalAddress();
    }

    /**
     * Open a local UDP listener forwarding to the default "connect" stream method.
     *
     * @return the local UDP address and port
     */
    @NotNull
    public InetSocketAddress connectUdp() {
        return connectUdp("connect");
    }

    /**
     * Open a local UDP listener forwarding to the specified stream method.
     *
     * @param method the stream method name
     * @return the local UDP address and port
     */
    @NotNull
    public InetSocketAddress connectUdp(@NotNull String method) {
        if (udpAdapter != null) {
            udpAdapter.close();
        }
        udpAdapter = UdpPortforwardAdapter.open(session, driverUuid, method);
        return udpAdapter.getLocalAddress();
    }

    @Override
    public void close() {
        if (tcpAdapter != null) {
            tcpAdapter.close();
        }
        if (udpAdapter != null) {
            udpAdapter.close();
        }
    }
}
