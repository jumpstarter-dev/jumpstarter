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
    // The framework provides a JUnit 5 extension, so it depends on (and re-exposes) the JUnit API.
    api(libs.junit.jupiter)
}
