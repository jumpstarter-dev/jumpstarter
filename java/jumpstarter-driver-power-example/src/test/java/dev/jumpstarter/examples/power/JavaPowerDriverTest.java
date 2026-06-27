package dev.jumpstarter.examples.power;

import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

import dev.jumpstarter.client.ExporterSession;
import dev.jumpstarter.driver.DriverHostServer;
import dev.jumpstarter.generated.power.PowerClient;
import jumpstarter.interfaces.power.v1.Power.PowerReading;
import java.io.File;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.Comparator;
import java.util.List;
import org.junit.jupiter.api.Test;

/**
 * Polyglot e2e for the <b>Java</b> path: serve the {@code grpc-java} {@link JavaPowerDriver} through
 * the Rust core (via the Java-callable {@link DriverHostServer}) and drive it with the generated
 * {@link PowerClient} — proving a stock Java grpc service is served generically with no per-interface
 * adapter, entirely from Java.
 */
class JavaPowerDriverTest {
    @Test
    void javaDriverServedAndDriven() throws Exception {
        Path dir = Files.createTempDirectory("jmp-java-power");
        String uds = dir.resolve("host.sock").toString();
        try (DriverHostServer server = DriverHostServer.serve(uds, new JavaPowerDriver.HostFactory());
                ExporterSession session = ExporterSession.connect(uds)) {
            PowerClient power = new PowerClient(session, "power");

            power.on();
            List<PowerReading> poweredOn = power.read();
            assertFalse(poweredOn.isEmpty(), "read() should yield readings");
            assertTrue(
                    poweredOn.stream().allMatch(r -> r.getVoltage() > 0.0),
                    "powered-on readings should be non-zero");

            power.off();
            List<PowerReading> poweredOff = power.read();
            assertTrue(
                    poweredOff.stream().allMatch(r -> r.getVoltage() == 0.0),
                    "powered-off readings should be zero");
        } finally {
            try (var paths = Files.walk(dir)) {
                paths.sorted(Comparator.reverseOrder()).map(Path::toFile).forEach(File::delete);
            }
        }
    }
}
