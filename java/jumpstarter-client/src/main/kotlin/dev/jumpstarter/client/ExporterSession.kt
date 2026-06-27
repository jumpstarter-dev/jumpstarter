package dev.jumpstarter.client

import io.grpc.Channel
import kotlinx.coroutines.runBlocking
import uniffi.jumpstarter_core.ClientSession

/**
 * The thin lease-client wrapper: reads `JUMPSTARTER_HOST` (set by `jmp shell`), connects the Rust
 * [ClientSession] (which owns auth, lease routing, SHM and the byte plane), and exposes a
 * [UniffiChannel] plus name→uuid lookup from `GetReport`. Together with [UniffiChannel] this is the
 * entire per-language runtime — everything else (the typed per-interface clients) is generated from
 * the `.proto` files.
 *
 * The JVM never opens its own connection to the exporter; the Rust core is the transport.
 */
class ExporterSession private constructor(
    val session: ClientSession,
    val report: DriverReport,
) : AutoCloseable {
    /** A gRPC channel that routes stub calls through the Rust core. */
    val channel: Channel by lazy { UniffiChannel(session) }

    fun requireDriver(name: String): String = report.requireByName(name).uuid

    fun optionalDriver(name: String): String? = report.findByName(name)?.uuid

    fun hasDriver(name: String): Boolean = report.findByName(name) != null

    override fun close() {
        // The Rust core / `jmp shell` owns the session lifecycle; nothing JVM-owned to release yet.
    }

    companion object {
        /** Connect using `JUMPSTARTER_HOST` from the environment (set by `jmp shell`). */
        @JvmStatic
        fun fromEnv(): ExporterSession {
            val host = System.getenv("JUMPSTARTER_HOST")
                ?: error("JUMPSTARTER_HOST not set — run inside `jmp shell`")
            return connect(host)
        }

        /** Connect to an explicit transport host (a Unix socket or `host:port` address). */
        @JvmStatic
        fun connect(host: String): ExporterSession = runBlocking {
            val session = ClientSession.connect(host)
            val report = DriverReport.parse(session.getReport())
            ExporterSession(session, report)
        }
    }
}
