plugins {
    `java-library`
    alias(libs.plugins.kotlin.jvm)
    // Tests load the Rust core over UniFFI (JNA), so they need the cdylib on the library path;
    // provided by the shared cargoBuildCore in :jumpstarter-core.
    id("dev.jumpstarter.cargo-cdylib")
}

kotlin {
    jvmToolchain(21)
}

dependencies {
    // The neutral runtime substrate: the UniFFI bindings (ClientSession/DriverException). `api`
    // because session types appear in this module's (and the generated clients') public signatures.
    api(project(":jumpstarter-core"))

    // Public API: the generated typed clients return protobuf types and route through stock grpc-java
    // stubs (JumpstarterChannel is an io.grpc.Channel), so the gRPC + protobuf runtime is exposed
    // transitively to client modules. (grpc/protobuf are neutral wire libraries — shared with the
    // driver side.) No grpc-kotlin: the generated clients use the grpc-java blocking stub.
    api(libs.grpc.api)
    api(libs.grpc.protobuf)
    api(libs.grpc.stub)
    api(libs.protobuf.java)
    api(libs.kotlinx.coroutines.core)
    // picocli is exposed so a custom client (subclassing the generated client) can annotate its CLI
    // subcommands with @Command — the JVM analog of a Python client's click `cli()`. This is the
    // CLIENT side's CLI dependency; it never reaches a driver host.
    api(libs.picocli)

    // Internal: gson parses the GetReport JSON (DriverReport). Client-side only.
    implementation(libs.gson)

    testImplementation(libs.junit.jupiter)
    testRuntimeOnly("org.junit.platform:junit-platform-launcher")
}

// --- Tests ------------------------------------------------------------------------------
// JNA's library path + the cargoBuildCore dependency are applied to every Test task by the
// `dev.jumpstarter.cargo-cdylib` convention.
tasks.test {
    useJUnitPlatform { excludeTags("integration") }
}

// Integration tests run inside `jmp shell` (JUMPSTARTER_HOST set).
//   ./gradlew :jumpstarter-client:integrationTest
tasks.register<Test>("integrationTest") {
    description = "Run integration tests inside a jmp shell session"
    group = "verification"
    testClassesDirs = sourceSets.test.get().output.classesDirs
    classpath = sourceSets.test.get().runtimeClasspath
    useJUnitPlatform { includeTags("integration") }
    environment("JUMPSTARTER_HOST", System.getenv("JUMPSTARTER_HOST") ?: "")
}
