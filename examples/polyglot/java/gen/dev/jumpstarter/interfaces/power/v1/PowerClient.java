package dev.jumpstarter.interfaces.power.v1;

import dev.jumpstarter.client.ExporterSession;
import dev.jumpstarter.client.UuidMetadataInterceptor;
import io.grpc.Channel;
import com.google.protobuf.Empty;
import java.util.Iterator;

/**
 * Auto-generated typed client for PowerInterface.
 *
 * <p>Control power delivery to a device under test.
 *
 * <p>Do not edit — regenerate with {@code jmp codegen}.
 */
public class PowerClient {

    private final PowerInterfaceGrpc.PowerInterfaceBlockingStub stub;

    public PowerClient(ExporterSession session, String driverName) {
        String uuid = session.getReport().findByName(driverName).getUuid();
        Channel channel = session.getChannel();
        this.stub = PowerInterfaceGrpc.newBlockingStub(channel)
            .withInterceptors(new UuidMetadataInterceptor(uuid));
    }

    /**
     * De-energize the power relay, cutting power to the DUT.
     */

    public void off() {
        stub.off(Empty.getDefaultInstance());
    }

    /**
     * Energize the power relay, delivering power to the DUT.
     */

    public void on() {
        stub.on(Empty.getDefaultInstance());
    }

    /**
     * Stream real-time power measurements from the DUT power rail.
     */

    public Iterator<Power.PowerReading> read() {
        return stub.read(Empty.getDefaultInstance());
    }

}
