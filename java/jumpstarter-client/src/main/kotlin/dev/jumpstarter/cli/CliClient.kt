package dev.jumpstarter.cli

/**
 * A JVM driver client that exposes a `j <driver> <subcommand>` CLI — implemented by a CUSTOM client
 * that subclasses the codegen-generated client. The JVM analog of a Python `DriverClient.cli()` click
 * group and a Rust clap `PowerCli`.
 *
 * [cliCommand] returns a picocli `@Command` object whose `@Command` methods are the subcommands; they
 * drive the inherited generated methods (over native gRPC) plus any custom client-side methods.
 */
interface CliClient {
    /** A picocli `@Command` object exposing this client's subcommands. */
    fun cliCommand(): Any
}
