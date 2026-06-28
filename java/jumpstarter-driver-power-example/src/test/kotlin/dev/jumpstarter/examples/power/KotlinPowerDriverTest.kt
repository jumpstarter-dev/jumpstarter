package dev.jumpstarter.examples.power

import dev.jumpstarter.client.ExporterSession
import dev.jumpstarter.driver.DriverHostServer
import dev.jumpstarter.driver.GrpcServiceDriverHostFactory
import dev.jumpstarter.generated.power.PowerClient
import org.junit.jupiter.api.Assertions.assertTrue
import org.junit.jupiter.api.Test
import java.nio.file.Files

/**
 * Polyglot e2e for the **Kotlin** path: serve the coroutine-based [KotlinPowerDriver] (suspend +
 * `Flow`) through the Rust core and drive it with the generated [PowerClient] — proving a native
 * Kotlin grpc-kotlin service is served generically (the runtime's `dispatch` awaits the coroutine
 * handler's async completion), with no per-interface adapter.
 */
class KotlinPowerDriverTest {
    @Test
    fun kotlinCoroutineDriverServedAndDriven() {
        val dir = Files.createTempDirectory("jmp-kotlin-power")
        val uds = dir.resolve("host.sock").toString()
        DriverHostServer.serve(uds, GrpcServiceDriverHostFactory.forDriver(KotlinPowerDriver::class.java, "power")).use {
            ExporterSession.connect(uds).use { session ->
                val power = PowerClient(session, "power")

                power.on()
                val poweredOn = power.read()
                assertTrue(poweredOn.isNotEmpty(), "read() should yield readings")
                assertTrue(poweredOn.all { it.voltage > 0.0 }, "powered-on readings should be non-zero")

                power.off()
                val poweredOff = power.read()
                assertTrue(poweredOff.all { it.voltage == 0.0 }, "powered-off readings should be zero")
            }
        }
        dir.toFile().deleteRecursively()
    }
}
