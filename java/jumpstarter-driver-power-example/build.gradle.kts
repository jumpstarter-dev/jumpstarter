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
    // ServeExporter reads the driver `type:` from the exporter config (YAML).
    implementation("org.yaml:snakeyaml:2.4")

    // The native test framework (lease acquisition + the JUnit adapters), for the example tests.
    testImplementation(project(":jumpstarter-testing"))
    testImplementation(libs.junit.jupiter) // JUnit 5/6 (self-contained tests + the extension example)
    testImplementation(libs.junit4) // JUnit 4 (the JumpstarterRule example; Tradefed/AOSP runs it)
    testRuntimeOnly("org.junit.platform:junit-platform-launcher")
}

tasks.test {
    // The self-contained driver tests run here. The lease-based examples need a real leased exporter:
    // the JUnit 5/6 one is tagged `integration` (excluded), and the JUnit 4 one is not run by the
    // platform at all (no Vintage engine — Tradefed/AOSP executes it with its own JUnit 4 runner).
    useJUnitPlatform { excludeTags("integration") }
}

// Run a controller-mediated exporter whose driver is selected by the config's `type:` (a JVM gRPC
// service class). Serves until killed:
//   ./gradlew :jumpstarter-driver-power-example:runPowerExporter -Pconfig=/path/to/exporter.yaml
val jnaPath = (extra["jumpstarterCdylibDir"] as File).path
tasks.register<JavaExec>("runPowerExporter") {
    group = "application"
    description = "Run a controller-mediated exporter serving the config-selected JVM driver"
    dependsOn(":jumpstarter-client:cargoBuildCore")
    mainClass.set("dev.jumpstarter.examples.power.ServeExporterKt")
    classpath = sourceSets.main.get().runtimeClasspath
    systemProperty("jna.library.path", jnaPath)
    if (project.hasProperty("config")) args(project.property("config").toString())
}

// Lease-based example tests — run inside a configured environment (a controller + a `power` exporter,
// or an outer `jmp shell`):  ./gradlew :jumpstarter-driver-power-example:integrationTest
tasks.register<Test>("integrationTest") {
    description = "Lease-based example tests (need a leased exporter)"
    group = "verification"
    testClassesDirs = sourceSets.test.get().output.classesDirs
    classpath = sourceSets.test.get().runtimeClasspath
    useJUnitPlatform { includeTags("integration") }
    environment("JUMPSTARTER_HOST", System.getenv("JUMPSTARTER_HOST") ?: "")
}
