// Convention: make this module's tests load the `jumpstarter-core` UniFFI cdylib from the sibling
// Rust workspace. The cdylib is built ONCE, by the `:jumpstarter-core` module (which owns the
// bindings); every other module's tests just depend on that single task and put it on the JNA
// library path. The cdylib path + rust dir are exposed as extra properties so `:jumpstarter-core` can
// wire its own cargoBuildCore + UniFFI tasks off the same locations.

import org.gradle.internal.os.OperatingSystem

val repoRoot: File = rootDir.parentFile
val rustDir: File = file("$repoRoot/rust")
val cdylibDir: File = file("$rustDir/target/debug")
val libName: String = when {
    OperatingSystem.current().isMacOsX -> "libjumpstarter_core.dylib"
    OperatingSystem.current().isWindows -> "jumpstarter_core.dll"
    else -> "libjumpstarter_core.so"
}
val cdylib: File = file("$cdylibDir/$libName")

extra["jumpstarterRustDir"] = rustDir
extra["jumpstarterCdylib"] = cdylib
extra["jumpstarterCdylibDir"] = cdylibDir

// The cdylib-producing task lives in the core module; reference it by path (lazy, so it resolves
// even though this convention also applies to that module before the task is registered).
val cargoBuildCorePath = ":jumpstarter-core:cargoBuildCore"

tasks.withType<Test>().configureEach {
    dependsOn(cargoBuildCorePath)
    systemProperty("jna.library.path", cdylibDir.path)
    testLogging {
        events("passed", "skipped", "failed")
        showStandardStreams = true
    }
}
