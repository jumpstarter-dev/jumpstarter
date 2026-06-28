package dev.jumpstarter.cli

/**
 * Marks a JVM driver CLIENT that exposes a `j <driver> <subcommand>` CLI — the JVM analog of the Rust
 * `#[client_cli]` attribute, and the client-side mirror of the driver `@JumpstarterDriver`. The
 * annotated class subclasses the generated client and provides `fun cliCommand(): Any` returning a
 * picocli `@Command` object; the `j` JVM dispatcher ([JMain]) loads it by its advertised `jvm:<fqn>`
 * label, checks this annotation, and runs the command. Only CLI-exposing clients are annotated — a
 * plain client library needs nothing. Same annotation in Kotlin and Java.
 */
@Target(AnnotationTarget.CLASS)
@Retention(AnnotationRetention.RUNTIME)
annotation class JumpstarterClientCli
