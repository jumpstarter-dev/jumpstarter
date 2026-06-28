package dev.jumpstarter.examples.power

import dev.jumpstarter.cli.JMain
import dev.jumpstarter.client.ExporterSession
import dev.jumpstarter.driver.DescriptorSets
import dev.jumpstarter.driver.DriverHostServer
import dev.jumpstarter.driver.GrpcServiceDriverHostFactory
import jumpstarter.interfaces.power.v1.Power
import org.junit.jupiter.api.Assertions.assertEquals
import org.junit.jupiter.api.Assertions.assertTrue
import org.junit.jupiter.api.Test
import java.nio.file.Files

/**
 * The **JVM client CLI** end to end: serve the driver advertising the JVM custom client
 * (`jvm:…CyclingPowerClient`), then route `j power off` / `j power cycle -w 0` through [JMain.dispatch]
 * — which resolves the client from the report label, builds its picocli command, and drives the
 * inherited generated methods over native gRPC. Proves a custom client *based on the generated client*
 * gets a working CLI.
 */
class CyclingPowerClientCliTest {
    @Test
    fun jvmClientCliCyclesViaJMain() {
        val dir = Files.createTempDirectory("jmp-jvm-cli")
        val uds = dir.resolve("host.sock").toString()
        // Advertise the JVM custom client (jvm:<fqn>) so JMain resolves and instantiates it.
        val factory = GrpcServiceDriverHostFactory(
            driverName = "power",
            descriptorSet = DescriptorSets.selfContained(Power.getDescriptor()),
            clientClass = "jvm:dev.jumpstarter.examples.power.CyclingPowerClient",
            service = { KotlinPowerDriver() },
        )
        DriverHostServer.serve(uds, factory).use {
            ExporterSession.connect(uds).use { session ->
                val probe = CyclingPowerClient(session, "power")

                // `j power off` via the CLI dispatch.
                assertEquals(0, JMain.dispatch(session, arrayOf("power", "off")))
                assertTrue(probe.readVoltages().all { it == 0.0 }, "off via CLI -> 0 V")

                // `j power cycle --wait 0` — the custom subcommand, driving off+on over native gRPC.
                assertEquals(0, JMain.dispatch(session, arrayOf("power", "cycle", "--wait", "0")))
                assertTrue(probe.readVoltages().all { it > 0.0 }, "cycle via CLI -> powered on")
            }
        }
        dir.toFile().deleteRecursively()
    }
}
