plugins {
    `java-library`
    alias(libs.plugins.kotlin.jvm)
    // Owns the single cdylib build + the JNA test wiring (build-logic); exposes the rust dir / cdylib
    // paths as extra properties this module's cargo + UniFFI tasks read.
    id("dev.jumpstarter.cargo-cdylib")
}

val rustDir = extra["jumpstarterRustDir"] as File
val cdylib = extra["jumpstarterCdylib"] as File
val uniffiOutDir = layout.buildDirectory.dir("generated/uniffi")

kotlin {
    jvmToolchain(21)
    sourceSets["main"].kotlin.srcDir(uniffiOutDir)
}

dependencies {
    // The generated UniFFI bindings (dev.jumpstarter.core.*) are plain Kotlin over JNA + coroutines;
    // both are public API because the bindings' own types (ClientSession, DriverHost, Flow returns)
    // appear in signatures the client/driver modules build on. JNA is implementation (loads the cdylib
    // at runtime; not referenced in public signatures), exposed transitively to dependents' runtime.
    api(libs.kotlinx.coroutines.core)
    implementation(libs.jna)
}

// --- The neutral runtime substrate: the jumpstarter-core UniFFI cdylib + its Kotlin bindings --------
// The JVM "steals the transport": the generated bindings drive jumpstarter-core over UniFFI (JNA loads
// libjumpstarter_core), so there is no JVM-side gRPC socket. This module owns the single cdylib build
// (every other module's `dev.jumpstarter.cargo-cdylib` test wiring depends on `cargoBuildCore` here);
// it carries NO client or driver machinery — only the bindings both sides share.
val cargoBuildCore by tasks.registering(Exec::class) {
    description = "Build the jumpstarter-core UniFFI cdylib"
    workingDir = rustDir
    commandLine("cargo", "build", "-p", "jumpstarter-core-uniffi")
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
    val uniffiConfig = file("$rustDir/jumpstarter-core-uniffi/uniffi.toml")
    commandLine(
        "cargo", "run", "-p", "jumpstarter-core-uniffi", "--bin", "uniffi-bindgen", "--",
        "generate", "--library", cdylib.path, "--language", "kotlin", "--no-format",
        "--config", uniffiConfig.path, // pins the package to dev.jumpstarter.core
        "--out-dir", outDir.path,
    )
    inputs.file(cdylib)
    inputs.file(uniffiConfig)
    outputs.dir(outDir)
}

tasks.named("compileKotlin") { dependsOn(generateUniffiBindings) }
