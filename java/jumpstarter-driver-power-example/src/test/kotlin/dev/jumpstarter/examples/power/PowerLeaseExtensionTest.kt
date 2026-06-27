package dev.jumpstarter.examples.power

import dev.jumpstarter.client.ExporterSession
import dev.jumpstarter.generated.power.PowerClient
import dev.jumpstarter.testing.JumpstarterExtension
import dev.jumpstarter.testing.JumpstarterLease
import org.junit.jupiter.api.Assertions.assertTrue
import org.junit.jupiter.api.Tag
import org.junit.jupiter.api.Test
import org.junit.jupiter.api.extension.ExtendWith

/**
 * Example — drive the power driver through a LEASED exporter using the **JUnit 5/6** framework. The
 * [JumpstarterExtension] acquires a lease (constrained by the [JumpstarterLease] `selector`, via the
 * embedded Rust core's `LeasedExporter` or an outer `jmp shell`) and injects the [ExporterSession];
 * the test drives the generated [PowerClient]. The identical code runs on JUnit 5 and JUnit 6.
 *
 * Tagged `integration` because it needs a real controller + a `power` exporter:
 *   `./gradlew :jumpstarter-driver-power-example:integrationTest`
 */
@Tag("integration")
@JumpstarterLease(selector = "example.com/board=power")
@ExtendWith(JumpstarterExtension::class)
class PowerLeaseExtensionTest {
    @Test
    fun powerOnOffThroughLease(session: ExporterSession) {
        val power = PowerClient(session, "power")

        power.on()
        assertTrue(power.read().all { it.voltage > 0.0 }, "powered on -> non-zero voltage")

        power.off()
        assertTrue(power.read().all { it.voltage == 0.0 }, "powered off -> zero voltage")
    }
}
