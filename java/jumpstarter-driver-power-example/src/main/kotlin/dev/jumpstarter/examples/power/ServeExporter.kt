package dev.jumpstarter.examples.power

import dev.jumpstarter.core.runExporter
import dev.jumpstarter.driver.ConfigDrivenHostFactory
import kotlinx.coroutines.runBlocking

/**
 * A controller-mediated exporter whose driver implementation is chosen by the EXPORTER CONFIG — the
 * JVM analog of how Python selects a driver via `type:`. The whole controller-mediated lifecycle
 * (register, lease, serve) runs in-process via the Rust core's `run_exporter`, with the per-lease
 * driver host built by the shared [ConfigDrivenHostFactory] (which reflectively instantiates the
 * `type:` service class and serves it through the generic `GrpcServiceDriverHost`).
 *
 * This is the JVM-embeds-the-core topology. To instead run as a per-driver host under the polyglot
 * `jmp run` hub, see `dev.jumpstarter.exporter.HostMain` (`jumpstarter-exporter-host`).
 *
 * Usage: `ServeExporter <exporter-config.yaml>` — e.g. a config whose power entry is
 * `type: dev.jumpstarter.examples.power.KotlinPowerDriver`.
 */
fun main(args: Array<String>) {
    val configPath = args.firstOrNull() ?: error("usage: ServeExporter <exporter-config.yaml>")
    runBlocking {
        runExporter(configPath, ConfigDrivenHostFactory.fromFile(configPath))
    }
}
