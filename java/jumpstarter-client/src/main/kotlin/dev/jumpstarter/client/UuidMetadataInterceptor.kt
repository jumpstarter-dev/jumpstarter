package dev.jumpstarter.client

import io.grpc.CallOptions
import io.grpc.Channel
import io.grpc.ClientCall
import io.grpc.ClientInterceptor
import io.grpc.ForwardingClientCall
import io.grpc.Metadata
import io.grpc.MethodDescriptor

/**
 * Standard gRPC client interceptor that stamps every outgoing call with the
 * `x-jumpstarter-driver-uuid` header so the exporter demux (and [JumpstarterChannel]) route to the right
 * driver instance. A stub bound to one driver wraps itself with one of these.
 */
class UuidMetadataInterceptor(private val uuid: String) : ClientInterceptor {
    override fun <ReqT, RespT> interceptCall(
        method: MethodDescriptor<ReqT, RespT>,
        callOptions: CallOptions,
        next: Channel,
    ): ClientCall<ReqT, RespT> =
        object : ForwardingClientCall.SimpleForwardingClientCall<ReqT, RespT>(
            next.newCall(method, callOptions),
        ) {
            override fun start(responseListener: Listener<RespT>, headers: Metadata) {
                headers.put(UUID_KEY, uuid)
                super.start(responseListener, headers)
            }
        }

    companion object {
        val UUID_KEY: Metadata.Key<String> =
            Metadata.Key.of("x-jumpstarter-driver-uuid", Metadata.ASCII_STRING_MARSHALLER)
    }
}
