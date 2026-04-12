package dev.jumpstarter.interfaces.network.v1;

import dev.jumpstarter.client.ExporterSession;
import dev.jumpstarter.client.UuidMetadataInterceptor;
import io.grpc.Channel;
import com.google.protobuf.Empty;

/**
 * Auto-generated typed client for NetworkInterface.
 *
 * <p>Bidirectional byte stream connection to a network endpoint.
 *
 * <p>Do not edit — regenerate with {@code jmp codegen}.
 */
public class NetworkClient {

    private final NetworkInterfaceGrpc.NetworkInterfaceBlockingStub stub;

    public NetworkClient(ExporterSession session, String driverName) {
        String uuid = session.getReport().findByName(driverName).getUuid();
        Channel channel = session.getChannel();
        this.stub = NetworkInterfaceGrpc.newBlockingStub(channel)
            .withInterceptors(new UuidMetadataInterceptor(uuid));
    }

}
