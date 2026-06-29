plugins {
    `java-library`
    alias(libs.plugins.kotlin.jvm)
    alias(libs.plugins.protobuf)
    // Tests load the Rust core over UniFFI (JNA), so they need the cdylib on the library path.
    id("dev.jumpstarter.cargo-cdylib")
    // Ships the `jumpstarter-exporter-host` + `jumpstarter-jvm-client` start scripts (the JVM driver
    // host + client CLI the polyglot hub / `j` spawn) with zero per-module host config: this module's
    // driver + client classes on the classpath + the reflective runtimes. The whole "host" is this
    // one plugin line. (This is the COMBINED example — it legitimately ships both launchers.)
    id("dev.jumpstarter.driver-host")
}

val repoRoot: File = rootDir.parentFile
val rustDir = extra["jumpstarterRustDir"] as File
val jnaPath = (extra["jumpstarterCdylibDir"] as File).path
// jumpstarter-codegen: the interface FileDescriptorSet (from the protobuf plugin) and the generated
// typed clients. Both live under build/ — the typed clients are generated each build, never committed.
val codegenDescriptor = layout.buildDirectory.file("generated/jumpstarter/interfaces.desc")
val codegenClientsDir = layout.buildDirectory.dir("generated/jumpstarter/clients")

kotlin {
    jvmToolchain(21)
    sourceSets["main"].kotlin.srcDir(codegenClientsDir)
}

dependencies {
    // The two framework runtimes — this combined example uses BOTH sides (a Kotlin/Java driver AND a
    // custom CLI client), so it deps both by design (its binaries pull both; that's the developer's
    // choice, exactly like the Rust combined power-example). A pure driver or pure client module would
    // dep only one.
    //   jumpstarter-driver: the generic host (GrpcServiceDriverHost/DriverHostServer/@JumpstarterDriver)
    //                       + the stock grpc-java/grpc-kotlin service bases + protobuf types.
    //   jumpstarter-client: ExporterSession + the @JumpstarterClientCli surface (picocli) the generated
    //                       PowerClient and custom CyclingPowerClient build on.
    implementation(project(":jumpstarter-driver"))
    implementation(project(":jumpstarter-client"))

    // javax.annotation.Generated used by grpc-java codegen output (the stubs generated below).
    compileOnly("org.apache.tomcat:annotations-api:6.0.53")

    // The native test framework (lease acquisition + the JUnit adapters), for the example tests.
    testImplementation(project(":jumpstarter-testing"))
    testImplementation(libs.junit.jupiter) // JUnit 5/6 (self-contained tests + the extension example)
    testImplementation(libs.junit4) // JUnit 4 (the JumpstarterRule example; Tradefed/AOSP runs it)
    testRuntimeOnly("org.junit.platform:junit-platform-launcher")
}

// --- Stock JVM gRPC stubs from the dedicated interfaces/ proto package -------------------
// The interface stubs are interface-SPECIFIC (power), so they are generated in the consuming module —
// NOT in the framework — mirroring how each Rust driver/client crate compiles its own .proto. The
// framework (jumpstarter-driver / jumpstarter-client) is interface-agnostic and carries no proto.
sourceSets {
    main {
        proto {
            srcDir("$repoRoot/interfaces/proto")
        }
    }
}

protobuf {
    protoc {
        artifact = "com.google.protobuf:protoc:${libs.versions.protobuf.get()}"
    }
    plugins {
        create("grpc") {
            artifact = "io.grpc:protoc-gen-grpc-java:${libs.versions.grpc.get()}"
        }
        // grpc-kotlin: generates the coroutine service base (`*CoroutineImplBase`, suspend fns +
        // `Flow`) so a Kotlin driver author writes idiomatic suspend functions.
        create("grpckt") {
            artifact = "io.grpc:protoc-gen-grpc-kotlin:${libs.versions.grpcKotlin.get()}:jdk8@jar"
        }
    }
    generateProtoTasks {
        all().forEach { task ->
            task.plugins {
                create("grpc")
                create("grpckt")
            }
            // Emit a self-contained FileDescriptorSet for jumpstarter-codegen to consume (so the
            // typed clients are generated at build time, never committed — see below).
            task.generateDescriptorSet = true
            task.descriptorSetOptions.includeImports = true
            task.descriptorSetOptions.path = codegenDescriptor.get().asFile.path
        }
    }
}

// --- Jumpstarter typed clients: generated each build from the interface descriptor set ----
// The committed code is the driver *implementations* + custom client only; the typed clients (and the
// gRPC stubs above) are fully codegenerated during the build, mirroring the Rust crate's build.rs.
val generateJumpstarterClients by tasks.registering(Exec::class) {
    description = "Generate the typed Jumpstarter clients from the interface descriptor set"
    dependsOn("generateProto")
    inputs.file(codegenDescriptor)
    outputs.dir(codegenClientsDir)
    workingDir = rustDir
    doFirst { codegenClientsDir.get().asFile.mkdirs() }
    commandLine(
        "cargo", "run", "-q", "-p", "jumpstarter-codegen", "--bin", "jumpstarter-codegen", "--",
        "--descriptor-set", codegenDescriptor.get().asFile.path,
        "--language", "kotlin", "--kind", "client", "--service", "PowerInterface",
        "--out", codegenClientsDir.get().asFile.path,
    )
}

tasks.named("compileKotlin") { dependsOn(generateJumpstarterClients) }

tasks.test {
    // The self-contained driver tests run here. The lease-based examples need a real leased exporter:
    // the JUnit 5/6 one is tagged `integration` (excluded), and the JUnit 4 one is not run by the
    // platform at all (no Vintage engine — Tradefed/AOSP executes it with its own JUnit 4 runner).
    useJUnitPlatform { excludeTags("integration") }
}

// Run a controller-mediated exporter whose driver is selected by the config's `type:` (a JVM gRPC
// service class). Serves until killed:
//   ./gradlew :jumpstarter-driver-power-example:runPowerExporter -Pconfig=/path/to/exporter.yaml
tasks.register<JavaExec>("runPowerExporter") {
    group = "application"
    description = "Run a controller-mediated exporter serving the config-selected JVM driver"
    dependsOn(":jumpstarter-core:cargoBuildCore")
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
