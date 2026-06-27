package dev.jumpstarter.client

import dev.jumpstarter.interfaces.power.PowerClient
import org.junit.jupiter.api.Assertions.assertTrue
import org.junit.jupiter.api.Tag
import org.junit.jupiter.api.Test

/**
 * End-to-end proof that a JVM caller drives a real Python driver through stock gRPC stubs whose
 * channel rides the Rust UniFFI transport — no JVM-side socket. Runs under `jmp shell` against a
 * direct `MockPower` exporter (JUMPSTARTER_HOST set):
 *
 *   jmp run --exporter-config e2e/exporters/exporter-direct-power.yaml \
 *           --tls-grpc-listener 19093 --tls-grpc-insecure
 *   jmp shell --tls-grpc 127.0.0.1:19093 --tls-grpc-insecure -- \
 *           ./gradlew :jumpstarter-client:integrationTest
 */
@Tag("integration")
class PowerNativeIT {
    @Test
    fun unaryAndServerStreamingThroughUniffiTransport() {
        ExporterSession.fromEnv().use { session ->
            val power = PowerClient(session, "power")

            // Unary calls (PowerInterface/On, /Off) over the UniFFI channel.
            power.on()
            power.off()

            // Server-streaming (PowerInterface/Read) — MockPower yields PowerReadings.
            val readings = power.read()
            assertTrue(readings.isNotEmpty(), "power.read() should yield at least one PowerReading")
            readings.forEach { assertTrue(it.voltage >= 0.0, "voltage should be present") }
        }
    }
}
