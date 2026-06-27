package dev.jumpstarter.interfaces.power

import com.google.protobuf.Empty
import dev.jumpstarter.client.ExporterSession
import dev.jumpstarter.client.UuidMetadataInterceptor
import jumpstarter.interfaces.power.v1.Power.PowerReading
import jumpstarter.interfaces.power.v1.PowerInterfaceGrpc

/**
 * A thin typed client over the stock `protoc`-generated [PowerInterfaceGrpc] blocking stub, bound to
 * one driver instance via [UuidMetadataInterceptor] and routed through the [ExporterSession]'s
 * UniFFI channel.
 *
 * Hand-written for the Phase-1 MVP only — this is exactly the shape `jmp codegen` will emit per
 * interface, so it becomes generated code (the runtime — session + channel — stays hand-written).
 */
class PowerClient(session: ExporterSession, driverName: String) {
    private val stub = PowerInterfaceGrpc.newBlockingStub(session.channel)
        .withInterceptors(UuidMetadataInterceptor(session.requireDriver(driverName)))

    fun on() {
        stub.on(Empty.getDefaultInstance())
    }

    fun off() {
        stub.off(Empty.getDefaultInstance())
    }

    fun read(): List<PowerReading> = stub.read(Empty.getDefaultInstance()).asSequence().toList()
}
