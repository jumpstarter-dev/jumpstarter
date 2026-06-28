package dev.jumpstarter.examples.power

import dev.jumpstarter.client.ExporterSession
import dev.jumpstarter.driver.DriverHostServer
import org.junit.jupiter.api.Assertions.assertTrue
import org.junit.jupiter.api.Test
import java.nio.file.Files

/**
 * The **custom client** subclassing the generated `PowerClient`: its `cycle` (a client-side method,
 * off+on) and `readVoltages` convenience are driven through the local harness (serve the Kotlin
 * driver over a UDS, connect a session) — proving an author can extend the generated client with
 * wrapper methods.
 */
class CyclingPowerClientTest {
    @Test
    fun customClientCycleLeavesDriverPoweredOn() {
        val dir = Files.createTempDirectory("jmp-cycling-power")
        val uds = dir.resolve("host.sock").toString()
        DriverHostServer.serve(uds, KotlinPowerDriverHostFactory()).use {
            ExporterSession.connect(uds).use { session ->
                val power = CyclingPowerClient(session, "power")

                power.off()
                assertTrue(power.readVoltages().all { it == 0.0 }, "off -> 0 V (via custom readVoltages)")

                // The custom client-side method: cycle ends powered on.
                power.cycle(waitMillis = 10)
                assertTrue(power.readVoltages().all { it > 0.0 }, "cycle ends powered on")
            }
        }
        dir.toFile().deleteRecursively()
    }
}
