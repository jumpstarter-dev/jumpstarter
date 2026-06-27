package dev.jumpstarter.client

import com.google.common.util.concurrent.MoreExecutors
import io.grpc.CallOptions
import io.grpc.Channel
import io.grpc.ClientCall
import io.grpc.Metadata
import io.grpc.MethodDescriptor
import io.grpc.Status
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.channels.Channel as MailboxChannel
import kotlinx.coroutines.launch
import dev.jumpstarter.core.ClientSession
import dev.jumpstarter.core.DriverException
import java.io.ByteArrayInputStream
import java.util.concurrent.Executor

/**
 * An [io.grpc.Channel] whose calls are carried not over a socket but **across UniFFI into the Rust
 * core**. Stock `protoc`-generated stubs marshal their request via the [MethodDescriptor]; this
 * channel hands the resulting opaque bytes to [ClientSession.nativeUnary] / [nativeServerStream] and
 * feeds the response bytes back through the stub's response marshaller. The JVM never opens its own
 * connection — `jumpstarter-core` owns the session, routing, SHM and byte plane.
 *
 * Everything above this channel is standard gRPC (stubs, [MethodDescriptor] marshallers, the
 * [ClientCall] SPI); the single non-standard hop is the byte transport into Rust.
 */
class JumpstarterChannel(private val session: ClientSession) : Channel() {
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)

    override fun authority(): String = "jumpstarter-uniffi"

    override fun <ReqT, RespT> newCall(
        method: MethodDescriptor<ReqT, RespT>,
        callOptions: CallOptions,
    ): ClientCall<ReqT, RespT> = JumpstarterClientCall(session, scope, method, callOptions)
}

/**
 * A single [ClientCall] backed by the Rust core. Supports unary and server-streaming methods (the two
 * shapes typed `@export` drivers produce); `@exportstream` byte channels ride [ClientSession.stream]
 * via a separate path.
 *
 * Listener callbacks are dispatched on [CallOptions.getExecutor] — a blocking stub parks on its own
 * `ThreadlessExecutor` draining runnables, so delivering callbacks off that executor would deadlock.
 * Flow control is honoured: server-streaming messages are only pulled from the Rust stream as the
 * consumer `request(n)`s them, so blocking stubs never see "too many responses".
 */
internal class JumpstarterClientCall<ReqT, RespT>(
    private val session: ClientSession,
    private val scope: CoroutineScope,
    private val method: MethodDescriptor<ReqT, RespT>,
    callOptions: CallOptions,
) : ClientCall<ReqT, RespT>() {
    private val executor: Executor = callOptions.executor ?: MoreExecutors.directExecutor()
    private var listener: Listener<RespT>? = null
    private var uuid: String = ""
    private var requestBytes: ByteArray = ByteArray(0)
    private val demand = MailboxChannel<Long>(MailboxChannel.UNLIMITED)
    private var job: Job? = null

    private val path: String get() = "/" + method.fullMethodName

    override fun start(responseListener: Listener<RespT>, headers: Metadata) {
        listener = responseListener
        uuid = headers.get(UuidMetadataInterceptor.UUID_KEY) ?: ""
    }

    override fun request(numMessages: Int) {
        demand.trySend(numMessages.toLong())
    }

    override fun sendMessage(message: ReqT) {
        // Marshal the request proto to opaque bytes using the stub's own request marshaller.
        requestBytes = method.streamRequest(message).readBytes()
    }

    override fun halfClose() {
        val l = listener ?: return
        val body = requestBytes
        job = scope.launch {
            try {
                post { l.onHeaders(Metadata()) }
                when (method.type) {
                    MethodDescriptor.MethodType.UNARY -> {
                        val response = session.nativeUnary(uuid, path, body)
                        post {
                            l.onMessage(parse(response))
                            l.onClose(Status.OK, Metadata())
                        }
                    }

                    MethodDescriptor.MethodType.SERVER_STREAMING -> {
                        val stream = session.nativeServerStream(uuid, path, body)
                        var credit = 0L
                        while (true) {
                            while (credit <= 0) credit += demand.receive()
                            val message = stream.next() ?: break
                            post { l.onMessage(parse(message)) }
                            credit--
                        }
                        post { l.onClose(Status.OK, Metadata()) }
                    }

                    else -> post {
                        l.onClose(
                            Status.UNIMPLEMENTED.withDescription(
                                "JumpstarterChannel does not support ${method.type} calls",
                            ),
                            Metadata(),
                        )
                    }
                }
            } catch (e: CancellationException) {
                throw e
            } catch (e: DriverException) {
                post {
                    l.onClose(
                        Status.INTERNAL.withDescription(e.message ?: e.toString()).withCause(e),
                        Metadata(),
                    )
                }
            } catch (e: Throwable) {
                post { l.onClose(Status.INTERNAL.withDescription(e.message).withCause(e), Metadata()) }
            }
        }
    }

    override fun cancel(message: String?, cause: Throwable?) {
        job?.cancel(CancellationException(message, cause))
        val l = listener ?: return
        post { l.onClose(Status.CANCELLED.withDescription(message).withCause(cause), Metadata()) }
    }

    override fun isReady(): Boolean = true

    private fun parse(bytes: ByteArray): RespT = method.parseResponse(ByteArrayInputStream(bytes))

    private fun post(block: () -> Unit) = executor.execute(block)
}
