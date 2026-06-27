import org.gradle.internal.os.OperatingSystem

plugins {
    `java-library`
    alias(libs.plugins.kotlin.jvm)
    alias(libs.plugins.protobuf)
}

val repoRoot: File = rootDir.parentFile
val rustDir = file("$repoRoot/rust")
val cdylibDir = file("$rustDir/target/debug")
val libName = when {
    OperatingSystem.current().isMacOsX -> "libjumpstarter_core.dylib"
    OperatingSystem.current().isWindows -> "jumpstarter_core.dll"
    else -> "libjumpstarter_core.so"
}
val cdylib = file("$cdylibDir/$libName")
val uniffiOutDir = layout.buildDirectory.dir("generated/uniffi")

kotlin {
    jvmToolchain(21)
    sourceSets["main"].kotlin.srcDir(uniffiOutDir)
}

dependencies {
    api(libs.grpc.api)
    implementation(libs.grpc.protobuf)
    implementation(libs.grpc.stub)
    implementation(libs.protobuf.java)
    implementation(libs.jna)
    implementation(libs.kotlinx.coroutines.core)
    implementation(libs.gson)

    // javax.annotation.Generated used by grpc-java codegen output.
    compileOnly("org.apache.tomcat:annotations-api:6.0.53")

    testImplementation(libs.junit.jupiter)
    testRuntimeOnly("org.junit.platform:junit-platform-launcher")
}

// --- Rust core: build the cdylib + generate the UniFFI Kotlin bindings -----------------
// The JVM client "steals the transport": the generated bindings drive jumpstarter-core over UniFFI
// (JNA loads libjumpstarter_core), so no JVM-side gRPC socket exists.

val cargoBuildCore by tasks.registering(Exec::class) {
    description = "Build the jumpstarter-core UniFFI cdylib"
    workingDir = rustDir
    commandLine("cargo", "build", "-p", "jumpstarter-core-uniffi")
    // Track the Rust sources so Gradle skips cargo (and the downstream binding regen + Kotlin
    // recompile) when nothing changed, and rebuilds when a `.rs`/manifest does — without this the
    // task either re-runs every build or goes stale on a source edit.
    inputs.files(
        fileTree(rustDir) {
            include("**/*.rs", "**/Cargo.toml")
            exclude("**/target/**")
        },
        file("$rustDir/Cargo.lock"),
    )
    outputs.file(cdylib)
}

val generateUniffiBindings by tasks.registering(Exec::class) {
    description = "Generate the Kotlin UniFFI bindings from the cdylib"
    dependsOn(cargoBuildCore)
    workingDir = rustDir
    val outDir = uniffiOutDir.get().asFile
    doFirst { outDir.mkdirs() }
    commandLine(
        "cargo", "run", "-p", "jumpstarter-core-uniffi", "--bin", "uniffi-bindgen", "--",
        "generate", "--library", cdylib.path, "--language", "kotlin", "--no-format",
        "--out-dir", outDir.path,
    )
    inputs.file(cdylib)
    outputs.dir(outDir)
}

tasks.named("compileKotlin") { dependsOn(generateUniffiBindings) }

// --- Stock JVM gRPC stubs from the dedicated interfaces/ proto package ------------------
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
    }
    generateProtoTasks {
        all().forEach { task ->
            task.plugins { create("grpc") }
        }
    }
}

// --- Tests: JNA must find the Rust cdylib ----------------------------------------------
tasks.withType<Test>().configureEach {
    dependsOn(cargoBuildCore)
    systemProperty("jna.library.path", cdylibDir.path)
    testLogging {
        events("passed", "skipped", "failed")
        showStandardStreams = true
    }
}

tasks.test {
    useJUnitPlatform { excludeTags("integration") }
}

// Integration tests run inside `jmp shell` (JUMPSTARTER_HOST set).
//   ./gradlew :jumpstarter-client:integrationTest
tasks.register<Test>("integrationTest") {
    description = "Run integration tests inside a jmp shell session"
    group = "verification"
    dependsOn(cargoBuildCore)
    testClassesDirs = sourceSets.test.get().output.classesDirs
    classpath = sourceSets.test.get().runtimeClasspath
    systemProperty("jna.library.path", cdylibDir.path)
    useJUnitPlatform { includeTags("integration") }
    environment("JUMPSTARTER_HOST", System.getenv("JUMPSTARTER_HOST") ?: "")
}
