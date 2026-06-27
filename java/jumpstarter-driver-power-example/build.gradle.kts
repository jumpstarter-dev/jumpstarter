plugins {
    `java-library`
    alias(libs.plugins.kotlin.jvm)
    // Tests load the Rust core over UniFFI (JNA), so they need the cdylib on the library path.
    id("dev.jumpstarter.cargo-cdylib")
}

kotlin {
    jvmToolchain(21)
}

dependencies {
    // The runtime — brings the generic driver host (GrpcServiceDriverHost/DriverHostServer), the
    // generated PowerClient, the stock grpc-java/grpc-kotlin service bases, and the protobuf types.
    implementation(project(":jumpstarter-client"))

    testImplementation(libs.junit.jupiter)
    testRuntimeOnly("org.junit.platform:junit-platform-launcher")
}

tasks.test {
    useJUnitPlatform()
}
