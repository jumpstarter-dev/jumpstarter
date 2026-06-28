package dev.jumpstarter.examples.power

import dev.jumpstarter.cli.JumpstarterClientCli
import dev.jumpstarter.client.ExporterSession
import dev.jumpstarter.generated.power.PowerClient
import jumpstarter.interfaces.power.v1.Power.PowerReading
import picocli.CommandLine.Command
import picocli.CommandLine.Option

/**
 * Example **custom client** — subclasses the codegen-generated, now-`open` [PowerClient] to add
 * client-side convenience methods the interface itself doesn't have ("custom interfaces on the client
 * side") AND a `j` CLI (`@JumpstarterClientCli` + `cliCommand()`). The JVM analog of subclassing
 * Python's `DriverClient` (with a `cli()`), and of Rust's `CyclingPowerClient` + `#[client_cli] PowerCli`.
 *
 * The inherited [on]/[off]/[read] drive the driver over native gRPC; the additions just compose them.
 * A driver advertises this client with the label `jvm:dev.jumpstarter.examples.power.CyclingPowerClient`.
 */
@JumpstarterClientCli
class CyclingPowerClient(session: ExporterSession, driverName: String) :
    PowerClient(session, driverName) {

    /** Custom client-side method (NOT an interface RPC): power-cycle — off, wait, on. */
    fun cycle(waitMillis: Long = 2_000) {
        off()
        Thread.sleep(waitMillis)
        on()
    }

    /** Convenience: just the voltages from a [read]. */
    fun readVoltages(): List<Double> = read().map(PowerReading::getVoltage)

    /** The picocli command exposing this client's `j` subcommands (found by [JumpstarterClientCli]). */
    fun cliCommand(): Any = Commands(this)

    /** The `j power <subcommand>` surface — picocli subcommands driving the (generated + custom) client. */
    @Command(name = "power", description = ["Control a power driver."])
    class Commands(private val client: CyclingPowerClient) {
        @Command(name = "on", description = ["Power on."])
        fun on() = client.on()

        @Command(name = "off", description = ["Power off."])
        fun off() = client.off()

        @Command(name = "cycle", description = ["Power cycle: off, wait, on."])
        fun cycle(
            @Option(
                names = ["--wait", "-w"],
                description = ["Milliseconds to wait between off and on."],
                defaultValue = "2000",
            )
            waitMillis: Long,
        ) = client.cycle(waitMillis)
    }
}
