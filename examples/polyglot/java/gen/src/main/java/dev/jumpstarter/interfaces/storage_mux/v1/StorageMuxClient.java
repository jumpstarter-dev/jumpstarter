package dev.jumpstarter.interfaces.storage_mux.v1;

import dev.jumpstarter.client.ExporterSession;
import dev.jumpstarter.client.UuidMetadataInterceptor;
import io.grpc.Channel;
import com.google.protobuf.Empty;

/**
 * Auto-generated typed client for StorageMuxInterface.
 *
 * <p>Switch storage media between host and device under test.
 *
 * <p>Do not edit — regenerate with {@code jmp codegen}.
 */
public class StorageMuxClient {

    private final StorageMuxInterfaceGrpc.StorageMuxInterfaceBlockingStub stub;

    public StorageMuxClient(ExporterSession session, String driverName) {
        String uuid = session.getReport().findByName(driverName).getUuid();
        Channel channel = session.getChannel();
        this.stub = StorageMuxInterfaceGrpc.newBlockingStub(channel)
            .withInterceptors(new UuidMetadataInterceptor(uuid));
    }

    /**
     * Connect the storage device to the device under test.
     */

    public void dut() {
        stub.dut(Empty.getDefaultInstance());
    }

    /**
     * Connect the storage device to the host.
     */

    public void host() {
        stub.host(Empty.getDefaultInstance());
    }

    /**
     * Disconnect the storage device from both host and DUT.
     */

    public void off() {
        stub.off(Empty.getDefaultInstance());
    }

    /**
     * Read the storage device contents to a resource handle.
     */

    public void read(String dst) {
        stub.read(StorageMux.ReadRequest.newBuilder().setDst(dst).build());
    }

    /**
     * Write an image from a resource handle to the storage device.
     */

    public void write(String src) {
        stub.write(StorageMux.WriteRequest.newBuilder().setSrc(src).build());
    }

}
