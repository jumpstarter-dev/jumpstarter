// Convention: this module ships the two JVM launcher start scripts of a Jumpstarter driver, mirroring
// the Rust per-crate host + client binaries:
//   - `jumpstarter-exporter-host` — the driver HOST the polyglot `jmp run` hub spawns for a
//     `runtime: jvm` entry (reflective `dev.jumpstarter.exporter.HostMain`).
//   - `jumpstarter-jvm-client`    — the client CLI `j` spawns for a `jvm:<fqn>` client (picocli
//     `dev.jumpstarter.cli.JMain`, which reflectively loads the client class).
// Both share this module's classpath (its driver + client classes) and the `jumpstarter-core` cdylib
// on the JNA library path. Applying this one plugin wires both — NO per-module launcher code/config.

plugins {
    application
}

// The cdylib dir (mirrors `dev.jumpstarter.cargo-cdylib`); both launchers load libjumpstarter_core
// over JNA, so it must be on jna.library.path.
val cdylibDir: File = file("${rootDir.parentFile}/rust/target/debug")

// The application plugin's primary start script: the driver host.
application {
    applicationName = "jumpstarter-exporter-host"
    mainClass.set("dev.jumpstarter.exporter.HostMainKt")
    applicationDefaultJvmArgs = listOf("-Djna.library.path=${cdylibDir.path}")
}

// A second start script in the same distribution: the JVM client CLI (`j` spawns it for `jvm:` clients).
val jvmClientScripts = tasks.register<CreateStartScripts>("jumpstarterJvmClientScripts") {
    applicationName = "jumpstarter-jvm-client"
    mainClass.set("dev.jumpstarter.cli.JMain")
    classpath = files(tasks.named("jar"), configurations.named("runtimeClasspath"))
    outputDir = layout.buildDirectory.dir("scripts-jvm-client").get().asFile
    defaultJvmOpts = listOf("-Djna.library.path=${cdylibDir.path}")
}
distributions.named("main") {
    contents {
        from(jvmClientScripts) { into("bin") }
    }
}

// The start scripts embed the Rust core cdylib path, so building the dist needs the cdylib built.
tasks.named("installDist") { dependsOn(":jumpstarter-core:cargoBuildCore") }
