package dev.jumpstarter.driver

import io.grpc.BindableService
import io.grpc.Metadata
import io.grpc.MethodDescriptor
import io.grpc.ServerCall
import io.grpc.ServerMethodDefinition
import io.grpc.Status
import kotlinx.coroutines.CompletableDeferred
import dev.jumpstarter.core.DriverException
import dev.jumpstarter.core.DriverHost
import dev.jumpstarter.core.DriverHostFactory
import dev.jumpstarter.core.DriverNode
import dev.jumpstarter.core.OpenStream
import java.util.UUID
import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.atomic.AtomicLong

private const val NAME_LABEL = "jumpstarter.dev/name"
private const val CLIENT_LABEL = "jumpstarter.dev/client"

/**
 * The generic, codegen-free JVM driver host — the Kotlin analog of the Rust
 * `jumpstarter_driver_runtime::serve_driver`.
 *
 * It adapts ANY stock grpc-java service (the author's `class … : PowerInterfaceImplBase()`) to the
 * Rust core's foreign [DriverHost] seam, with no per-interface generated code: inbound opaque
 * native `(path, body)` calls are routed to the matching [ServerMethodDefinition] from the service's
 * own `bindService()`, decoded with that method's stock marshaller, dispatched through its stock
 * [io.grpc.ServerCallHandler] (so the author writes a plain grpc-java service and never touches the
 * wire codec), and the responses re-encoded. The author supplies only the service, its descriptor
 * (`DescriptorSets.selfContained(Power.getDescriptor())`), and the instance name.
 */
class GrpcServiceDriverHost(
    service: BindableService,
    private val descriptorSet: ByteArray,
    private val driverName: String,
    private val clientClass: String,
) : DriverHost {
    private val methods: Map<String, ServerMethodDefinition<*, *>> =
        service.bindService().methods.associateBy { it.methodDescriptor.fullMethodName }
    private val streams = ConcurrentHashMap<ULong, Iterator<ByteArray>>()
    private val nextHandle = AtomicLong(1)
    private val driverUuid: String = UUID.randomUUID().toString()

    override suspend fun describe(): List<DriverNode> = listOf(
        DriverNode(
            uuid = driverUuid,
            parentUuid = null,
            labels = mapOf(NAME_LABEL to driverName, CLIENT_LABEL to clientClass),
            description = null,
            methodsDescription = emptyMap(),
            descriptorSet = descriptorSet,
        ),
    )

    override suspend fun forwardUnary(uuid: String, path: String, body: ByteArray): ByteArray {
        val method = lookup(path)
        // Decline non-unary methods with `Unimplemented` so the Rust core falls through to
        // `forwardServerStream` (the core's seam tries the unary path first for every call).
        if (method.methodDescriptor.type != MethodDescriptor.MethodType.UNARY) {
            throw DriverException.Unimplemented("'$path' is not a unary method")
        }
        val responses = dispatch(method, body)
        return responses.singleOrNull()
            ?: throw DriverException.Unknown("unary method at '$path' produced ${responses.size} responses")
    }

    override suspend fun forwardServerStream(uuid: String, path: String, body: ByteArray): ULong {
        val responses = dispatch(lookup(path), body)
        val handle = nextHandle.getAndIncrement().toULong()
        streams[handle] = responses.iterator()
        return handle
    }

    override suspend fun forwardStreamNext(handle: ULong): ByteArray? =
        streams[handle]?.let { if (it.hasNext()) it.next() else null }

    override suspend fun forwardStreamClose(handle: ULong) {
        streams.remove(handle)
    }

    /** Resolve the [ServerMethodDefinition] registered at a gRPC method `path`. */
    private fun lookup(path: String): ServerMethodDefinition<*, *> =
        methods[path.removePrefix("/")]
            ?: throw DriverException.Unimplemented("no native method at path '$path'")

    /**
     * Drive `method`'s stock [io.grpc.ServerCallHandler] synchronously through a minimal capturing
     * [ServerCall]: decode the request with the method's marshaller, deliver it to the listener, and
     * collect every message the service sends. grpc-java's `ImplBase` services complete inline on
     * the calling thread, so the responses are captured by the time the listener completes.
     */
    private suspend fun <ReqT, RespT> dispatch(
        method: ServerMethodDefinition<ReqT, RespT>,
        body: ByteArray,
    ): List<ByteArray> {
        val md: MethodDescriptor<ReqT, RespT> = method.methodDescriptor
        val request: ReqT = md.parseRequest(body.inputStream())
        // Responses may be sent from another thread (a grpc-kotlin coroutine handler); guard the list
        // and hand off completion via a Deferred the ServerCall completes in `close()`.
        val captured = java.util.Collections.synchronizedList(mutableListOf<ByteArray>())
        val done = CompletableDeferred<Status>()

        val call = object : ServerCall<ReqT, RespT>() {
            override fun request(numMessages: Int) {}
            override fun sendHeaders(headers: Metadata) {}
            override fun sendMessage(message: RespT) {
                captured.add(md.streamResponse(message).readBytes())
            }
            override fun close(status: Status, trailers: Metadata) {
                done.complete(status)
            }
            override fun isCancelled(): Boolean = false
            override fun getMethodDescriptor(): MethodDescriptor<ReqT, RespT> = md
        }

        // Drive the stock `ServerCallHandler`. A grpc-java `ImplBase` completes inline (so `done` is
        // already complete); a grpc-kotlin `CoroutineImplBase` completes asynchronously on its
        // dispatcher — `done.await()` handles both uniformly.
        val listener = method.serverCallHandler.startCall(call, Metadata())
        listener.onReady()
        listener.onMessage(request)
        listener.onHalfClose()
        val status = done.await()
        listener.onComplete()

        if (!status.isOk) {
            throw DriverException.Unknown(
                "${status.code}: ${status.description ?: "native method failed"}",
            )
        }
        return captured.toList()
    }

    // A proto-first host serves everything through the native `forward*` seams above; the JSON
    // `driverCall`/`@export`-streaming surfaces and the byte plane are unused.
    override suspend fun driverCall(uuid: String, methodName: String, argsJson: String): String =
        throw DriverException.Unimplemented("native-only host: no JSON driverCall")

    override suspend fun streamingOpen(uuid: String, methodName: String, argsJson: String): ULong =
        throw DriverException.Unimplemented("native-only host: no JSON streaming")

    override suspend fun streamingNext(handle: ULong): String? =
        throw DriverException.Unimplemented("native-only host: no JSON streaming")

    override suspend fun streamingClose(handle: ULong) =
        throw DriverException.Unimplemented("native-only host: no JSON streaming")

    override suspend fun openStream(requestJson: String): OpenStream =
        throw DriverException.Unimplemented("native-only host: no byte streams")

    override suspend fun streamRead(handle: ULong): ByteArray =
        throw DriverException.Unimplemented("native-only host: no byte streams")

    override suspend fun streamWrite(handle: ULong, data: ByteArray): Unit =
        throw DriverException.Unimplemented("native-only host: no byte streams")

    override suspend fun streamCloseWrite(handle: ULong): Unit =
        throw DriverException.Unimplemented("native-only host: no byte streams")

    override suspend fun streamClose(handle: ULong): Unit =
        throw DriverException.Unimplemented("native-only host: no byte streams")
}

/**
 * A [DriverHostFactory] that mints a fresh [GrpcServiceDriverHost] per lease from a service factory
 * — the JVM entrypoint for a proto-first driver. The author supplies how to build their service,
 * its descriptor, the instance name, and the client class; everything else is generic.
 */
class GrpcServiceDriverHostFactory(
    private val driverName: String,
    private val descriptorSet: ByteArray,
    private val clientClass: String,
    private val service: () -> BindableService,
) : DriverHostFactory {
    override fun newHost(): DriverHost =
        GrpcServiceDriverHost(service(), descriptorSet, driverName, clientClass)

    companion object {
        /**
         * Build a host factory from a driver CLASS reflectively — instantiate the stock gRPC service,
         * derive its descriptor, and read [JumpstarterDriver.client] for the `jumpstarter.dev/client`
         * label — so an `@JumpstarterDriver`-annotated driver needs NO hand-written factory.
         */
        @JvmStatic
        fun forDriver(
            driverClass: Class<out io.grpc.BindableService>,
            driverName: String,
        ): GrpcServiceDriverHostFactory {
            val annotation = driverClass.getAnnotation(JumpstarterDriver::class.java)
                ?: error("${driverClass.name} is not annotated with @JumpstarterDriver")
            val descriptor = descriptorOf(driverClass.getDeclaredConstructor().newInstance())
            return GrpcServiceDriverHostFactory(driverName, descriptor, annotation.client) {
                driverClass.getDeclaredConstructor().newInstance()
            }
        }

        /** The interface's self-contained descriptor, taken from the stock service's proto file. */
        internal fun descriptorOf(service: io.grpc.BindableService): ByteArray {
            val schema = service.bindService().serviceDescriptor.schemaDescriptor
            val file = (schema as? io.grpc.protobuf.ProtoFileDescriptorSupplier)?.fileDescriptor
                ?: error("service ${service::class.java.name} exposes no proto FileDescriptor")
            return DescriptorSets.selfContained(file)
        }
    }
}
