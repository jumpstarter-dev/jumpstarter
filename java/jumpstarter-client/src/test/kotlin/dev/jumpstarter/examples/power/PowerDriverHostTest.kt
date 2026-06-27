package dev.jumpstarter.examples.power

import dev.jumpstarter.client.ExporterSession
import dev.jumpstarter.interfaces.power.PowerClient
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.launch
import org.junit.jupiter.api.Assertions.assertTrue
import org.junit.jupiter.api.Test
import java.io.File
import java.nio.file.Files

/**
 * Self-contained polyglot e2e: serve the Kotlin-authored [ExamplePowerDriver] through the Rust core
 * (`serveDriverHost`), then drive it with the generated [PowerClient] over the UniFFI transport —
 * proving a JVM-authored *driver* and a JVM *client* interoperate through `jumpstarter-core`, with no
 * external `jmp shell` or exporter. Runs in the standard `./gradlew test` suite (loads the cdylib via
 * JNA; the build copies it onto `jna.library.path`).
 */
class PowerDriverHostTest {
    @Test
    fun examplePowerDriverServedAndDrivenThroughUniffi() {
        val dir = Files.createTempDirectory("jmp-jvm-power")
        val uds = dir.resolve("host.sock").toString()
        val serverScope = CoroutineScope(Dispatchers.IO + SupervisorJob())
        val serverJob = serverScope.launch {
            uniffi.jumpstarter_core.serveDriverHost(uds, PowerDriverHostFactory())
        }
        try {
            val socket = File(uds)
            var waited = 0
            while (!socket.exists() && waited < 400) {
                Thread.sleep(25)
                waited++
            }
            assertTrue(socket.exists(), "serveDriverHost never bound the socket")

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
        } finally {
            serverJob.cancel()
            File(uds).delete()
            dir.toFile().delete()
        }
    }
}
