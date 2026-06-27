pluginManagement {
    // Shared convention plugins (cargo cdylib + JNA test wiring, …) live in the build-logic
    // included build.
    includeBuild("build-logic")
    repositories {
        gradlePluginPortal()
        mavenCentral()
    }
}

rootProject.name = "jumpstarter-jvm"

// The JVM monorepo, mirroring the Rust/Python workspaces:
//   jumpstarter-client               — the runtime (transport client + driver-host serving).
//   jumpstarter-testing              — the native test framework (JUnit extension over the Rust core).
//   jumpstarter-driver-power-example — example drivers + their tests (split by language).
// The exporter/hub runtime itself is the embedded Rust core (UniFFI), so it needs no JVM project.
include("jumpstarter-client")
include("jumpstarter-testing")
include("jumpstarter-driver-power-example")
