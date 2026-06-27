plugins {
    `java-library`
    alias(libs.plugins.kotlin.jvm)
}

kotlin {
    jvmToolchain(21)
}

dependencies {
    // The runtime — exposes ExporterSession + the UniFFI bindings (LeasedExporter). `api` because the
    // framework's public types (JumpstarterLease.session) are runtime types.
    api(project(":jumpstarter-client"))

    // JUnit adapters for compat across generations. The harness-agnostic core (JumpstarterLease)
    // needs no JUnit at all; the JUnit 5/6 extension and the JUnit 4 rule compile against the
    // respective APIs as `compileOnly`, so the consumer brings whichever JUnit generation they run
    // (Jupiter 5.x/6.x, or JUnit 4 for AOSP/Tradefed) — the unused adapter is simply never loaded.
    compileOnly(libs.junit.jupiter.api) // JUnit 5 / 6 (the Jupiter extension API is stable across 5→6)
    compileOnly(libs.junit4) // JUnit 4 (AOSP 14 / Tradefed)
}
