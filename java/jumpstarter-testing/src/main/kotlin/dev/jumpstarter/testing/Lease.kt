package dev.jumpstarter.testing

import dev.jumpstarter.client.ExporterSession
import dev.jumpstarter.core.LeasedExporter
import kotlinx.coroutines.runBlocking
import java.nio.file.Path
import java.nio.file.Paths

/**
 * An autonomously-acquired lease plus a connected [ExporterSession] — the JVM analog of Python's
 * `jumpstarter.common.utils.lease()`.
 *
 * It drives the Rust core's shared auto-acquire capability ([LeasedExporter]): resolve the client
 * config, acquire a lease against the controller, serve it on a local socket, and connect a session
 * — the *same* mechanism every language runtime uses, so no JVM-specific lease logic is needed. Use
 * it directly (`Lease.acquire().use { ... it.session ... }`) or through the JUnit
 * [JumpstarterExtension]. [close] releases the lease.
 *
 * When running inside a `jmp shell` (i.e. already leased, `JUMPSTARTER_HOST` set), prefer
 * [fromEnvironment] — it connects to the existing lease instead of acquiring a new one.
 */
class Lease private constructor(
    private val exporter: LeasedExporter?,
    /** The connected session — build typed clients from it, e.g. `PowerClient(session, "power")`. */
    val session: ExporterSession,
) : AutoCloseable {
    override fun close() {
        try {
            session.close()
        } finally {
            // Only release a lease we acquired; a `fromEnvironment` session is owned by the outer shell.
            exporter?.let { runBlocking { it.release() } }
        }
    }

    companion object {
        /**
         * Autonomously acquire a lease from the client config `alias` and connect a session.
         * `selector` constrains exporter selection; `durationSecs` is the lease duration.
         */
        @JvmStatic
        @JvmOverloads
        fun acquire(
            selector: String? = null,
            alias: String = "default",
            durationSecs: Long = 1800,
        ): Lease {
            val configPath = clientConfigHome().resolve("clients").resolve("$alias.yaml").toString()
            val exporter = runBlocking {
                LeasedExporter.acquire(configPath, selector, null, null, durationSecs.toULong())
            }
            val session = ExporterSession.connect(exporter.jumpstarterHost())
            return Lease(exporter, session)
        }

        /**
         * Connect to the lease an outer `jmp shell` already holds (`JUMPSTARTER_HOST`). Does not
         * acquire or release a lease. Throws if `JUMPSTARTER_HOST` is unset.
         */
        @JvmStatic
        fun fromEnvironment(): Lease {
            val host = System.getenv("JUMPSTARTER_HOST")
            require(!host.isNullOrEmpty()) {
                "JUMPSTARTER_HOST is not set — run inside a `jmp shell`, or use acquire()"
            }
            return Lease(null, ExporterSession.connect(host))
        }

        /**
         * The default a test harness uses: connect to the lease an outer `jmp shell` already holds
         * (`JUMPSTARTER_HOST`, in which case `selector` is moot — the shell already chose the
         * exporter), else autonomously [acquire] one constrained by `selector`. Shared by the JUnit
         * 4/5/6 adapters.
         */
        @JvmStatic
        @JvmOverloads
        fun fromEnvironmentOrAcquire(
            selector: String? = null,
            alias: String = "default",
            durationSecs: Long = 1800,
        ): Lease =
            if (!System.getenv("JUMPSTARTER_HOST").isNullOrEmpty()) fromEnvironment()
            else acquire(selector, alias, durationSecs)

        /** Mirror of Python's client config home: `$JMP_CLIENT_CONFIG_HOME` or `$XDG_CONFIG_HOME/jumpstarter`. */
        private fun clientConfigHome(): Path {
            System.getenv("JMP_CLIENT_CONFIG_HOME")?.takeIf { it.isNotEmpty() }?.let { return Paths.get(it) }
            val xdg = System.getenv("XDG_CONFIG_HOME")
            val base =
                if (!xdg.isNullOrEmpty()) Paths.get(xdg)
                else Paths.get(System.getProperty("user.home"), ".config")
            return base.resolve("jumpstarter")
        }
    }
}
