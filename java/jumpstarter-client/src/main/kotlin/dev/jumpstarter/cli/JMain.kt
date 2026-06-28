package dev.jumpstarter.cli

import dev.jumpstarter.client.ExporterSession
import picocli.CommandLine
import kotlin.system.exitProcess

/**
 * The JVM client CLI — `j <driver> <subcommand> [args]` for JVM driver clients (the analog of the
 * Rust `j` native path and the Python `j` click dispatch). It connects the leased exporter
 * (`JUMPSTARTER_HOST`), resolves the driver's client class from the report's `jumpstarter.dev/client`
 * label (a `jvm:<fqn>` for a JVM client), instantiates that CUSTOM client (a subclass of the
 * generated client implementing [CliClient]), and runs its picocli command over native gRPC. Native
 * `j` delegates `jvm:`-client drivers here.
 */
object JMain {
    /** The label carrying a driver instance's client class. */
    const val CLIENT_LABEL = "jumpstarter.dev/client"

    @JvmStatic
    fun main(args: Array<String>) {
        val session = try {
            ExporterSession.fromEnv()
        } catch (e: Exception) {
            System.err.println("jumpstarter: ${e.message}")
            exitProcess(1)
        }
        exitProcess(dispatch(session, args))
    }

    /** Testable core: route `<driver> <subcommand...>` to the driver's JVM client CLI. */
    fun dispatch(session: ExporterSession, args: Array<String>): Int {
        val driver = args.firstOrNull() ?: run {
            System.err.println("usage: j <driver> <subcommand> [args]")
            return 2
        }
        val instance = session.report.findByName(driver) ?: run {
            System.err.println("no driver named '$driver' in the exporter report")
            return 1
        }
        val label = instance.labels[CLIENT_LABEL] ?: run {
            System.err.println("driver '$driver' advertises no client class")
            return 1
        }
        // `jvm:` marks a JVM client (mirrors the driver `type: jvm:<fqn>`); the rest is the class FQN.
        val fqn = label.removePrefix("jvm:")
        val client = try {
            Class.forName(fqn)
                .getConstructor(ExporterSession::class.java, String::class.java)
                .newInstance(session, driver)
        } catch (e: ReflectiveOperationException) {
            System.err.println("could not load JVM client '$fqn': ${e.message}")
            return 1
        }
        if (client !is CliClient) {
            System.err.println("client '$fqn' has no CLI (does not implement CliClient)")
            return 1
        }
        return CommandLine(client.cliCommand()).execute(*args.copyOfRange(1, args.size))
    }
}
