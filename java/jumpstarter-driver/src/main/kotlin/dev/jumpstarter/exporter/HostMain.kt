package dev.jumpstarter.exporter

import dev.jumpstarter.core.serveDriverHost
import dev.jumpstarter.driver.ConfigDrivenHostFactory
import kotlinx.coroutines.runBlocking

/**
 * `jumpstarter-exporter-host --serve <uds>` — the JVM driver-host subprocess the polyglot exporter
 * hub spawns for a `runtime: jvm` (or `type: jvm:<fqn>`) entry, exactly as it spawns
 * `python -m jumpstarter.exporter_host` for Python and `jumpstarter-driver-<crate>-host` for Rust.
 *
 * It obeys the same language-neutral host contract: the single-entry config arrives on **stdin**,
 * the driver is reflectively loaded from its `type:` ([ConfigDrivenHostFactory]), and the embedded
 * Rust core (UniFFI `serveDriverHost`) serves the driver-host seam on `--serve <uds>` until the hub
 * kills the process. The host's classpath supplies the driver classes (a driver module ships its own
 * `jumpstarter-exporter-host` start script bundling them), so one reflective launcher serves any
 * driver on its classpath — the JVM analog of "available in the package path".
 */
fun main(args: Array<String>) {
    val uds = parseServe(args)
        ?: error("usage: jumpstarter-exporter-host --serve <uds>  (single-entry config on stdin)")

    // Exit if the hub dies before it can kill us (parent-death watchdog via JMP_HUB_PID) — the JVM
    // analog of the Rust `exit_when_orphaned`; UniFFI's `serveDriverHost` does not install one.
    exitWhenOrphaned()

    // The hub streams the single-entry config YAML on stdin, closed with EOF.
    val yaml = System.`in`.readBytes().decodeToString()
    val factory = ConfigDrivenHostFactory.fromYaml(yaml)

    // Serve the driver-host seam until the hub SIGKILLs us at lease end (or a signal arrives).
    runBlocking { serveDriverHost(uds, factory) }
}

/** Extract the `--serve <uds>` value from argv. */
private fun parseServe(args: Array<String>): String? {
    val i = args.indexOf("--serve")
    return if (i >= 0 && i + 1 < args.size) args[i + 1] else null
}

/**
 * Hard-exit this process if the hub (`JMP_HUB_PID`) vanishes — robust even when this host is
 * reparented to init after an ungraceful hub death (SIGKILL/crash/OOM), where the hub's
 * `kill_on_drop` never runs. A no-op when not spawned by the hub (no `JMP_HUB_PID`).
 */
private fun exitWhenOrphaned() {
    val hubPid = System.getenv("JMP_HUB_PID")?.toLongOrNull() ?: return
    Thread {
        while (true) {
            Thread.sleep(250)
            // Empty Optional => the pid is gone; present but !isAlive => it has exited.
            val alive = ProcessHandle.of(hubPid).map { it.isAlive }.orElse(false)
            if (!alive) Runtime.getRuntime().halt(0)
        }
    }.apply {
        name = "parent-death-watch"
        isDaemon = true
        start()
    }
}
