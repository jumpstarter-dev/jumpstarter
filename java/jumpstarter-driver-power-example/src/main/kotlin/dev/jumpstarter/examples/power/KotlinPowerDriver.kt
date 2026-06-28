package dev.jumpstarter.examples.power

import com.google.protobuf.Empty
import dev.jumpstarter.driver.JumpstarterDriver
import jumpstarter.interfaces.power.v1.Power.PowerReading
import jumpstarter.interfaces.power.v1.PowerInterfaceGrpcKt.PowerInterfaceCoroutineImplBase
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.flow

/**
 * The **Kotlin** proto-first power driver: a NATIVE coroutine gRPC service — `suspend fun` for the
 * unary methods, `Flow` for the server-streaming one — implementing the stock grpc-kotlin
 * [PowerInterfaceCoroutineImplBase]. No descriptor-building, no adapter, and (via [JumpstarterDriver])
 * NO hand-written host factory: the reflective host instantiates this class, derives its descriptor,
 * and reads the annotation's `client`. Authoring a Kotlin Jumpstarter driver is idiomatic suspend code.
 */
@JumpstarterDriver(client = "jumpstarter_driver_power.client.PowerClient")
class KotlinPowerDriver : PowerInterfaceCoroutineImplBase(Dispatchers.Default) {
    @Volatile
    var isOn: Boolean = false
        private set

    override suspend fun on(request: Empty): Empty {
        isOn = true
        return Empty.getDefaultInstance()
    }

    override suspend fun off(request: Empty): Empty {
        isOn = false
        return Empty.getDefaultInstance()
    }

    /** Voltage/current readings — non-zero only while powered on. */
    override fun read(request: Empty): Flow<PowerReading> = flow {
        val readings = if (isOn) listOf(5.0 to 1.0, 5.1 to 1.2) else listOf(0.0 to 0.0)
        for ((voltage, current) in readings) {
            emit(PowerReading.newBuilder().setVoltage(voltage).setCurrent(current).build())
        }
    }
}
