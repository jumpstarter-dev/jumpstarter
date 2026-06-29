plugins {
    `java-library`
    alias(libs.plugins.kotlin.jvm)
    // Tests (and any driver host) load the Rust core over UniFFI (JNA), so they need the cdylib on the
    // library path; provided by the shared cargoBuildCore in :jumpstarter-core.
    id("dev.jumpstarter.cargo-cdylib")
}

kotlin {
    jvmToolchain(21)
}

dependencies {
    // The neutral runtime substrate: the UniFFI bindings (DriverHost/serveDriverHost/DriverException).
    // `api` because the driver-host seam types appear in this module's public signatures.
    api(project(":jumpstarter-core"))

    // Public API: a driver author implements the stock grpc-java `…ImplBase` / grpc-kotlin
    // `…CoroutineImplBase` service bases, so the gRPC + protobuf runtime is exposed transitively to
    // driver modules. (grpc/protobuf are neutral wire libraries — shared with the client side — not
    // client-or-driver machinery.)
    api(libs.grpc.api)
    api(libs.grpc.protobuf)
    api(libs.grpc.stub)
    api(libs.grpc.kotlin.stub)
    api(libs.protobuf.java)
    api(libs.kotlinx.coroutines.core)

    // Internal: snakeyaml parses the single-entry exporter config the hub streams to
    // `jumpstarter-exporter-host` (ConfigDrivenHostFactory). This is the DRIVER side's only extra
    // dependency — the client's picocli/gson never reach a driver host.
    implementation("org.yaml:snakeyaml:2.4")

    testImplementation(libs.junit.jupiter)
    testRuntimeOnly("org.junit.platform:junit-platform-launcher")
}

tasks.test {
    useJUnitPlatform { excludeTags("integration") }
}
