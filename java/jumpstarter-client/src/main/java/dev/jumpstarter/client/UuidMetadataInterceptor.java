package dev.jumpstarter.client;

import io.grpc.*;
import org.jetbrains.annotations.NotNull;

/**
 * gRPC client interceptor that injects the {@code x-jumpstarter-driver-uuid}
 * metadata header into every outgoing call. This routes the call to the correct
 * driver instance within the exporter.
 */
public final class UuidMetadataInterceptor implements ClientInterceptor {

    private static final Metadata.Key<String> UUID_KEY =
            Metadata.Key.of("x-jumpstarter-driver-uuid", Metadata.ASCII_STRING_MARSHALLER);

    private final String uuid;

    public UuidMetadataInterceptor(@NotNull String uuid) {
        this.uuid = uuid;
    }

    @Override
    public <ReqT, RespT> ClientCall<ReqT, RespT> interceptCall(
            MethodDescriptor<ReqT, RespT> method,
            CallOptions callOptions,
            Channel next) {
        return new ForwardingClientCall.SimpleForwardingClientCall<>(
                next.newCall(method, callOptions)) {
            @Override
            public void start(Listener<RespT> responseListener, Metadata headers) {
                headers.put(UUID_KEY, uuid);
                super.start(responseListener, headers);
            }
        };
    }
}
