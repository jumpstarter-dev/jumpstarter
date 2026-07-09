package dev.jumpstarter.examples.power

import dev.jumpstarter.client.ExporterSession
import dev.jumpstarter.generated.power.PowerClient
import org.junit.jupiter.api.Assertions.assertTrue
import org.junit.jupiter.api.Tag
import org.junit.jupiter.api.Test

/**
 * Act 2 of the rust-core demo: a JUnit/Kotlin test drives a real **Python** power driver through
 * the **generated** Kotlin [PowerClient] — stock grpc-java stubs whose channel rides the Rust
 * UniFFI transport. There is no JVM-side gRPC socket; the Rust core owns all I/O.
 *
 * This file lives in `examples/rust-core-demo/act2-kotlin-python/` so demo readers see exactly
 * what runs; the gradle module `:jumpstarter-driver-power-example` compiles it via an external
 * test srcDir. Run it under a controller lease — `serve.sh` + `run.sh` next to this file, or:
 *
 *   jmp run --exporter demo-mock                                     # terminal A
 *   cd java && jmp shell --client demo-client --selector example.com/dut=mock -- \
 *       ./gradlew :jumpstarter-driver-power-example:integrationTest \
 *       --tests "*PowerNativeIT"                                     # terminal B
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
