package dev.jumpstarter.examples.power

import dev.jumpstarter.client.ExporterSession
import dev.jumpstarter.driver.DriverHostServer
import dev.jumpstarter.driver.GrpcServiceDriverHostFactory
import dev.jumpstarter.generated.device.ExampleRigDevice
import org.junit.jupiter.api.Assertions.assertTrue
import org.junit.jupiter.api.Test
import java.nio.file.Files

/**
 * End-to-end test for the GENERATED typed device wrapper — the JVM sibling of
 * `python/examples/exporter-device-example` and `rust/jumpstarter-device-example`.
 *
 * The served tree matches this module's committed `exporter.yaml` (the wrapper's source of
 * truth): one `power` node hosting [KotlinPowerDriver]. `ExampleRigDevice(session)` resolves the
 * node by NAME PATH from the report, and — because `interfaces/registry/native.yaml` advertises
 * the custom jvm client for this driver type — `device.power` is statically a
 * [CyclingPowerClient], custom extension methods included.
 */
class ExampleRigDeviceTest {
    @Test
    fun deviceWrapperBindsTheCustomClientAndRoundTrips() {
        val dir = Files.createTempDirectory("jmp-device-example")
        val uds = dir.resolve("host.sock").toString()
        DriverHostServer.serve(
            uds,
            GrpcServiceDriverHostFactory.forDriver(KotlinPowerDriver::class.java, "power"),
        ).use {
            ExporterSession.connect(uds).use { session ->
                val device = ExampleRigDevice(session)

                // Statically typed as the CUSTOM client (registry-advertised) — the extension
                // methods are available without casts.
                val power: CyclingPowerClient = device.power

                power.on()
                assertTrue(power.readVoltages().all { it > 0.0 }, "powered-on readings")

                power.off()
                assertTrue(power.readVoltages().all { it == 0.0 }, "powered-off readings")

                // The custom client-side composition: cycle ends powered on.
                power.cycle(waitMillis = 10)
                assertTrue(power.readVoltages().all { it > 0.0 }, "cycle ends powered on")
            }
        }
        dir.toFile().deleteRecursively()
    }
}
