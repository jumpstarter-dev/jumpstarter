package dev.jumpstarter.driver.network;

import com.google.protobuf.ByteString;
import dev.jumpstarter.client.DriverReport;
import dev.jumpstarter.client.ExporterSession;
import dev.jumpstarter.client.UuidMetadataInterceptor;
import io.grpc.Channel;
import io.grpc.stub.StreamObserver;
import org.jetbrains.annotations.NotNull;
import org.jetbrains.annotations.Nullable;

import java.io.IOException;
import java.net.DatagramPacket;
import java.net.DatagramSocket;
import java.net.InetSocketAddress;
import java.net.ServerSocket;
import java.net.Socket;
import java.net.SocketAddress;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.CopyOnWriteArrayList;
import java.util.logging.Level;
import java.util.logging.Logger;

/**
 * Typed client for network drivers that provide TCP and UDP connectivity
 * to a device under test.
 *
 * <p>Uses native gRPC bidirectional streaming via {@code NetworkInterface.Connect}
 * to bridge local sockets to the remote network endpoint.
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

    private static final Logger logger = Logger.getLogger(NetworkClient.class.getName());
    private static final int BUFFER_SIZE = 32768;
    private static final int UDP_BUFFER_SIZE = 65535;

    private final String driverUuid;
    private final NetworkInterfaceGrpc.NetworkInterfaceBlockingStub blockingStub;
    private final NetworkInterfaceGrpc.NetworkInterfaceStub asyncStub;
    private @Nullable TcpBridge tcpBridge;
    private @Nullable UdpBridge udpBridge;

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
     * Open a local TCP listener that bridges each accepted connection to
     * the remote network endpoint via {@code NetworkInterface.Connect}.
     *
     * @return the local TCP address and port
     */
    @NotNull
    public InetSocketAddress connectTcp() {
        if (tcpBridge != null) {
            tcpBridge.close();
        }
        tcpBridge = TcpBridge.start(asyncStub);
        return tcpBridge.getLocalAddress();
    }

    /**
     * Open a local UDP listener that bridges datagrams to the remote
     * network endpoint via {@code NetworkInterface.Connect}.
     *
     * @return the local UDP address and port
     */
    @NotNull
    public InetSocketAddress connectUdp() {
        if (udpBridge != null) {
            udpBridge.close();
        }
        udpBridge = UdpBridge.start(asyncStub);
        return udpBridge.getLocalAddress();
    }

    @Override
    public void close() {
        if (tcpBridge != null) {
            tcpBridge.close();
        }
        if (udpBridge != null) {
            udpBridge.close();
        }
    }

    /**
     * Bridges accepted TCP connections to a gRPC bidi stream.
     */
    private static final class TcpBridge implements AutoCloseable {
        private final ServerSocket serverSocket;
        private final CopyOnWriteArrayList<Socket> activeSockets = new CopyOnWriteArrayList<>();
        private final CopyOnWriteArrayList<StreamObserver<Network.StreamData>> activeObservers = new CopyOnWriteArrayList<>();
        private volatile boolean closed = false;

        private TcpBridge(ServerSocket serverSocket) {
            this.serverSocket = serverSocket;
        }

        static TcpBridge start(NetworkInterfaceGrpc.NetworkInterfaceStub asyncStub) {
            try {
                ServerSocket ss = new ServerSocket();
                ss.setReuseAddress(true);
                ss.bind(new InetSocketAddress("127.0.0.1", 0));

                TcpBridge bridge = new TcpBridge(ss);

                Thread acceptThread = new Thread(() -> bridge.acceptLoop(asyncStub),
                        "jumpstarter-net-tcp-accept-" + ss.getLocalPort());
                acceptThread.setDaemon(true);
                acceptThread.start();

                logger.info("TCP bridge listening on " + ss.getLocalSocketAddress());
                return bridge;
            } catch (IOException e) {
                throw new RuntimeException("Failed to create TCP bridge listener", e);
            }
        }

        InetSocketAddress getLocalAddress() {
            return (InetSocketAddress) serverSocket.getLocalSocketAddress();
        }

        @Override
        public void close() {
            if (closed) return;
            closed = true;
            try { serverSocket.close(); } catch (IOException ignored) {}
            for (Socket s : activeSockets) {
                try { s.close(); } catch (IOException ignored) {}
            }
            for (StreamObserver<Network.StreamData> obs : activeObservers) {
                try { obs.onCompleted(); } catch (Exception ignored) {}
            }
        }

        private void acceptLoop(NetworkInterfaceGrpc.NetworkInterfaceStub asyncStub) {
            while (!closed) {
                try {
                    Socket client = serverSocket.accept();
                    activeSockets.add(client);
                    bridgeConnection(asyncStub, client);
                } catch (IOException e) {
                    if (!closed) {
                        logger.log(Level.WARNING, "TCP bridge accept error", e);
                    }
                }
            }
        }

        private void bridgeConnection(NetworkInterfaceGrpc.NetworkInterfaceStub asyncStub, Socket client) {
            // Response observer: server → socket
            StreamObserver<Network.StreamData> responseObserver = new StreamObserver<>() {
                @Override
                public void onNext(Network.StreamData value) {
                    try {
                        byte[] data = value.getPayload().toByteArray();
                        client.getOutputStream().write(data);
                        client.getOutputStream().flush();
                    } catch (IOException e) {
                        cleanup(client, null);
                    }
                }

                @Override
                public void onError(Throwable t) {
                    cleanup(client, null);
                }

                @Override
                public void onCompleted() {
                    cleanup(client, null);
                }
            };

            // Open bidi stream
            StreamObserver<Network.StreamData> requestObserver = asyncStub.connect(responseObserver);
            activeObservers.add(requestObserver);

            // Socket → server thread
            Thread s2c = new Thread(() -> {
                try {
                    byte[] buf = new byte[BUFFER_SIZE];
                    int n;
                    while ((n = client.getInputStream().read(buf)) != -1) {
                        Network.StreamData msg = Network.StreamData.newBuilder()
                                .setPayload(ByteString.copyFrom(buf, 0, n))
                                .build();
                        requestObserver.onNext(msg);
                    }
                    requestObserver.onCompleted();
                } catch (IOException e) {
                    if (!closed) {
                        try { requestObserver.onError(e); } catch (Exception ignored) {}
                    }
                } finally {
                    cleanup(client, requestObserver);
                }
            }, "jumpstarter-net-tcp-s2c-" + client.getPort());
            s2c.setDaemon(true);
            s2c.start();
        }

        private void cleanup(Socket client, @Nullable StreamObserver<Network.StreamData> requestObserver) {
            try { client.close(); } catch (IOException ignored) {}
            activeSockets.remove(client);
            if (requestObserver != null) {
                activeObservers.remove(requestObserver);
            }
        }
    }

    /**
     * Bridges UDP datagrams to a gRPC bidi stream. Each unique sender
     * address gets its own bidi stream.
     */
    private static final class UdpBridge implements AutoCloseable {
        private final DatagramSocket socket;
        private final Map<SocketAddress, StreamObserver<Network.StreamData>> channels = new ConcurrentHashMap<>();
        private volatile boolean closed = false;

        private UdpBridge(DatagramSocket socket) {
            this.socket = socket;
        }

        static UdpBridge start(NetworkInterfaceGrpc.NetworkInterfaceStub asyncStub) {
            try {
                DatagramSocket ds = new DatagramSocket(new InetSocketAddress("127.0.0.1", 0));
                UdpBridge bridge = new UdpBridge(ds);

                Thread recvThread = new Thread(() -> bridge.receiveLoop(asyncStub),
                        "jumpstarter-net-udp-recv-" + ds.getLocalPort());
                recvThread.setDaemon(true);
                recvThread.start();

                logger.info("UDP bridge listening on " + ds.getLocalSocketAddress());
                return bridge;
            } catch (IOException e) {
                throw new RuntimeException("Failed to create UDP bridge listener", e);
            }
        }

        InetSocketAddress getLocalAddress() {
            return (InetSocketAddress) socket.getLocalSocketAddress();
        }

        @Override
        public void close() {
            if (closed) return;
            closed = true;
            socket.close();
            for (StreamObserver<Network.StreamData> obs : channels.values()) {
                try { obs.onCompleted(); } catch (Exception ignored) {}
            }
            channels.clear();
        }

        private void receiveLoop(NetworkInterfaceGrpc.NetworkInterfaceStub asyncStub) {
            byte[] buf = new byte[UDP_BUFFER_SIZE];

            while (!closed) {
                try {
                    DatagramPacket packet = new DatagramPacket(buf, buf.length);
                    socket.receive(packet);

                    SocketAddress sender = packet.getSocketAddress();
                    StreamObserver<Network.StreamData> requestObserver = channels.computeIfAbsent(sender,
                            addr -> openStream(asyncStub, addr));

                    byte[] data = new byte[packet.getLength()];
                    System.arraycopy(packet.getData(), packet.getOffset(), data, 0, packet.getLength());

                    Network.StreamData msg = Network.StreamData.newBuilder()
                            .setPayload(ByteString.copyFrom(data))
                            .build();
                    requestObserver.onNext(msg);
                } catch (IOException e) {
                    if (!closed) {
                        logger.log(Level.WARNING, "UDP bridge receive error", e);
                    }
                }
            }
        }

        private StreamObserver<Network.StreamData> openStream(
                NetworkInterfaceGrpc.NetworkInterfaceStub asyncStub, SocketAddress sender) {
            StreamObserver<Network.StreamData> responseObserver = new StreamObserver<>() {
                @Override
                public void onNext(Network.StreamData value) {
                    try {
                        byte[] data = value.getPayload().toByteArray();
                        DatagramPacket reply = new DatagramPacket(data, 0, data.length, sender);
                        socket.send(reply);
                    } catch (IOException e) {
                        if (!closed) {
                            logger.log(Level.FINE, "UDP bridge send error", e);
                        }
                    }
                }

                @Override
                public void onError(Throwable t) {
                    channels.remove(sender);
                }

                @Override
                public void onCompleted() {
                    channels.remove(sender);
                }
            };

            return asyncStub.connect(responseObserver);
        }
    }
}
