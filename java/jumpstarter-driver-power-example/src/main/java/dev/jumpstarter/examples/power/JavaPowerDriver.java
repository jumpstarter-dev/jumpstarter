package dev.jumpstarter.examples.power;

import com.google.protobuf.Empty;
import dev.jumpstarter.driver.JumpstarterDriver;
import io.grpc.stub.StreamObserver;
import jumpstarter.interfaces.power.v1.Power.PowerReading;
import jumpstarter.interfaces.power.v1.PowerInterfaceGrpc.PowerInterfaceImplBase;

/**
 * The <b>Java</b> proto-first power driver: a plain {@code grpc-java} service implementing the stock
 * {@code PowerInterfaceImplBase} (callback / {@link StreamObserver} style — Java has no coroutines).
 * No descriptor-building, no adapter: the generic {@link GrpcServiceDriverHost} serves this stock
 * service to the Rust core. Authoring a Java Jumpstarter driver is exactly "implement the stock gRPC
 * service base class".
 */
@JumpstarterDriver(client = "jumpstarter_driver_power.client.PowerClient")
public class JavaPowerDriver extends PowerInterfaceImplBase {
    private volatile boolean on = false;

    @Override
    public void on(Empty request, StreamObserver<Empty> responseObserver) {
        on = true;
        responseObserver.onNext(Empty.getDefaultInstance());
        responseObserver.onCompleted();
    }

    @Override
    public void off(Empty request, StreamObserver<Empty> responseObserver) {
        on = false;
        responseObserver.onNext(Empty.getDefaultInstance());
        responseObserver.onCompleted();
    }

    @Override
    public void read(Empty request, StreamObserver<PowerReading> responseObserver) {
        double[][] readings = on
                ? new double[][] {{5.0, 1.0}, {5.1, 1.2}}
                : new double[][] {{0.0, 0.0}};
        for (double[] vc : readings) {
            responseObserver.onNext(
                    PowerReading.newBuilder().setVoltage(vc[0]).setCurrent(vc[1]).build());
        }
        responseObserver.onCompleted();
    }
}
