package dev.jumpstarter.devices;

import dev.jumpstarter.client.ExporterSession;
import dev.jumpstarter.interfaces.power.v1.PowerClient;
import dev.jumpstarter.interfaces.storage_mux.v1.StorageMuxClient;
import dev.jumpstarter.driver.network.NetworkClient;
import org.jetbrains.annotations.Nullable;

/**
 * Auto-generated typed wrapper for ExporterClass example-board.
 *
 * <p>Do not edit — regenerate with {@code jmp codegen} when the ExporterClass changes.
 */
public class ExampleBoardDevice implements AutoCloseable {

    /** PowerClient — required by ExporterClass */
    private final PowerClient power;
    /** StorageMuxClient — required by ExporterClass */
    private final StorageMuxClient storage;
    /** NetworkClient — optional, may be null by ExporterClass */
    @Nullable
    private final NetworkClient network;

    public ExampleBoardDevice(ExporterSession session) {
        this.power = new PowerClient(session, "power");
        this.storage = new StorageMuxClient(session, "storage");
        this.network = session.hasDriver("network") ? new NetworkClient(session, "network") : null;
    }

    public PowerClient power() { return power; }

    public StorageMuxClient storage() { return storage; }

    @Nullable
    public NetworkClient network() { return network; }

    @Override
    public void close() {
        // Session cleanup is handled by ExporterSession
    }
}
