package dev.jumpstarter.examples.power

import dev.jumpstarter.driver.DescriptorSets
import jumpstarter.interfaces.power.v1.Power
import uniffi.jumpstarter_core.DriverException
import uniffi.jumpstarter_core.DriverHost
import uniffi.jumpstarter_core.DriverHostFactory
import uniffi.jumpstarter_core.DriverNode
import uniffi.jumpstarter_core.OpenStream
import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.atomic.AtomicLong

private const val POWER_UUID = "11111111-1111-1111-1111-111111111111"
private const val NAME_LABEL = "jumpstarter.dev/name"

/**
 * An example `PowerInterface` driver implemented in Kotlin — the server-side counterpart of the
 * generated client. It holds on/off state and emits power readings. This is what authoring a
 * Jumpstarter driver in a JVM language looks like (a future `jmp codegen` would scaffold the host
 * glue below from the same `.proto`).
 */
class ExamplePowerDriver {
    @Volatile
    var isOn: Boolean = false
        private set

    fun on() {
        isOn = true
    }

    fun off() {
        isOn = false
    }

    /** Voltage/current readings — non-zero only while powered on. */
    fun read(): List<Pair<Double, Double>> =
        if (isOn) listOf(5.0 to 1.0, 5.1 to 1.2) else listOf(0.0 to 0.0)
}

/**
 * Adapts an [ExamplePowerDriver] to the Rust core's [DriverHost] foreign-trait seam: the core
 * dispatches decoded native `PowerInterface` calls into these JSON-shaped methods (the `@export`
 * names `on`/`off`/`read`), and serves the driver's native gRPC service from the advertised
 * [DriverNode.descriptorSet]. Power has no `@exportstream` byte channels, so those methods are
 * unimplemented.
 */
class PowerDriverHost(private val driver: ExamplePowerDriver = ExamplePowerDriver()) : DriverHost {
    private val streams = ConcurrentHashMap<ULong, Iterator<String>>()
    private val nextHandle = AtomicLong(1)

    override suspend fun describe(): List<DriverNode> = listOf(
        DriverNode(
            uuid = POWER_UUID,
            parentUuid = null,
            labels = mapOf(NAME_LABEL to "power"),
            description = "Example power driver implemented in Kotlin",
            methodsDescription = emptyMap(),
            descriptorSet = DescriptorSets.selfContained(Power.getDescriptor()),
        ),
    )

    override suspend fun driverCall(uuid: String, methodName: String, argsJson: String): String =
        when (methodName) {
            "on" -> { driver.on(); "null" }
            "off" -> { driver.off(); "null" }
            else -> throw DriverException.Unimplemented("power: no unary method '$methodName'")
        }

    override suspend fun streamingOpen(uuid: String, methodName: String, argsJson: String): ULong {
        if (methodName != "read") {
            throw DriverException.Unimplemented("power: no streaming method '$methodName'")
        }
        val readings = driver.read()
            .map { (voltage, current) -> """{"voltage":$voltage,"current":$current}""" }
            .iterator()
        val handle = nextHandle.getAndIncrement().toULong()
        streams[handle] = readings
        return handle
    }

    override suspend fun streamingNext(handle: ULong): String? =
        streams[handle]?.let { if (it.hasNext()) it.next() else null }

    override suspend fun streamingClose(handle: ULong) {
        streams.remove(handle)
    }

    // PowerInterface has no @exportstream byte channels.
    override suspend fun openStream(requestJson: String): OpenStream =
        throw DriverException.Unimplemented("power: no byte streams")

    override suspend fun streamRead(handle: ULong): ByteArray =
        throw DriverException.Unimplemented("power: no byte streams")

    override suspend fun streamWrite(handle: ULong, data: ByteArray): Unit =
        throw DriverException.Unimplemented("power: no byte streams")

    override suspend fun streamCloseWrite(handle: ULong): Unit =
        throw DriverException.Unimplemented("power: no byte streams")

    override suspend fun streamClose(handle: ULong): Unit =
        throw DriverException.Unimplemented("power: no byte streams")
}

/** Mints a fresh [PowerDriverHost] per lease — the foreign side of the core's `serveDriverHost`. */
class PowerDriverHostFactory : DriverHostFactory {
    override fun newHost(): DriverHost = PowerDriverHost()
}
