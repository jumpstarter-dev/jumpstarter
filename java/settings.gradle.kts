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

// The JVM monorepo, mirroring the Rust/Python workspaces and the client/driver crate split:
//   jumpstarter-core                 — the neutral runtime substrate: the cdylib build + UniFFI
//                                      bindings (dev.jumpstarter.core.*); both sides dep this.
//   jumpstarter-client               — the CLIENT runtime + `j` CLI (transport client, picocli) —
//                                      ONLY client machinery, no driver-host code.
//   jumpstarter-driver               — the DRIVER host runtime + exporter-host entrypoint — ONLY
//                                      driver machinery, no client/CLI code.
//   jumpstarter-testing              — the native test framework (JUnit extension over the Rust core).
//   jumpstarter-driver-power-example — example drivers + custom client + their tests (deps BOTH sides,
//                                      and owns the interface proto + typed-client codegen).
// The exporter/hub runtime itself is the embedded Rust core (UniFFI), so it needs no JVM project.
include("jumpstarter-core")
include("jumpstarter-client")
include("jumpstarter-driver")
include("jumpstarter-testing")
include("jumpstarter-driver-power-example")
