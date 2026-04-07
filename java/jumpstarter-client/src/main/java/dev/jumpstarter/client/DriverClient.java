package dev.jumpstarter.client;

import com.google.protobuf.Value;
import io.grpc.StatusRuntimeException;
import jumpstarter.v1.ExporterServiceGrpc;
import jumpstarter.v1.Jumpstarter;
import org.jetbrains.annotations.NotNull;
import org.jetbrains.annotations.Nullable;

import java.util.ArrayList;
import java.util.Iterator;
import java.util.List;

/**
 * Generic driver method dispatch client.
 *
 * <p>Calls driver methods by string name with dynamic arguments via the
 * {@code ExporterService.DriverCall} RPC. Arguments and return values are
 * serialized through {@link com.google.protobuf.Value} using {@link ValueCodec}.
 *
 * <p>Example:
 * <pre>{@code
 * DriverClient power = session.driverClient("power");
 * power.call("on");
 * Object voltage = power.call("read_voltage");
 * }</pre>
 */
public final class DriverClient {

    private final String uuid;
    private final ExporterServiceGrpc.ExporterServiceBlockingStub stub;

    DriverClient(@NotNull String uuid,
                 @NotNull ExporterServiceGrpc.ExporterServiceBlockingStub stub) {
        this.uuid = uuid;
        this.stub = stub;
    }

    /** The UUID of the driver instance this client dispatches to. */
    @NotNull
    public String getUuid() {
        return uuid;
    }

    /**
     * Call a driver method by name with the given arguments.
     *
     * @param method the method name (e.g. "on", "off", "read_voltage")
     * @param args   arguments to pass (supported types: null, Boolean, Number, String, List, Map)
     * @return the result decoded from {@link Value}, or null
     * @throws DriverCallException if the RPC fails
     */
    @Nullable
    public Object call(@NotNull String method, @Nullable Object... args) {
        Jumpstarter.DriverCallRequest.Builder request = Jumpstarter.DriverCallRequest.newBuilder()
                .setUuid(uuid)
                .setMethod(method);

        if (args != null) {
            for (Object arg : args) {
                request.addArgs(ValueCodec.encode(arg));
            }
        }

        try {
            Jumpstarter.DriverCallResponse response = stub.driverCall(request.build());
            return ValueCodec.decode(response.getResult());
        } catch (StatusRuntimeException e) {
            throw new DriverCallException(method, e);
        }
    }

    /**
     * Call a streaming driver method by name.
     *
     * @param method the method name
     * @param args   arguments to pass
     * @return an iterator of decoded results
     * @throws DriverCallException if the RPC fails
     */
    @NotNull
    public Iterator<Object> streamingCall(@NotNull String method, @Nullable Object... args) {
        Jumpstarter.StreamingDriverCallRequest.Builder request =
                Jumpstarter.StreamingDriverCallRequest.newBuilder()
                        .setUuid(uuid)
                        .setMethod(method);

        if (args != null) {
            for (Object arg : args) {
                request.addArgs(ValueCodec.encode(arg));
            }
        }

        try {
            Iterator<Jumpstarter.StreamingDriverCallResponse> responses =
                    stub.streamingDriverCall(request.build());

            return new Iterator<>() {
                @Override
                public boolean hasNext() {
                    return responses.hasNext();
                }

                @Override
                public Object next() {
                    return ValueCodec.decode(responses.next().getResult());
                }
            };
        } catch (StatusRuntimeException e) {
            throw new DriverCallException(method, e);
        }
    }

    /**
     * Collect all results from a streaming driver call into a list.
     *
     * @param method the method name
     * @param args   arguments to pass
     * @return list of decoded results
     * @throws DriverCallException if the RPC fails
     */
    @NotNull
    public List<Object> streamingCallToList(@NotNull String method, @Nullable Object... args) {
        List<Object> results = new ArrayList<>();
        Iterator<Object> it = streamingCall(method, args);
        while (it.hasNext()) {
            results.add(it.next());
        }
        return results;
    }

    /**
     * Exception thrown when a driver call fails.
     */
    public static class DriverCallException extends RuntimeException {
        private final String method;

        public DriverCallException(String method, StatusRuntimeException cause) {
            super("DriverCall '" + method + "' failed: " + cause.getStatus() + " - " + cause.getMessage(), cause);
            this.method = method;
        }

        public String getMethod() {
            return method;
        }
    }
}
