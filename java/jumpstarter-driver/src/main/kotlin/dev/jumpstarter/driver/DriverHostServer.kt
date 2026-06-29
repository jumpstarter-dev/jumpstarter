package dev.jumpstarter.driver

import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.launch
import dev.jumpstarter.core.DriverHostFactory
import dev.jumpstarter.core.serveDriverHost
import java.io.File

/**
 * Serves a [DriverHostFactory] over a UDS on a background coroutine — a Java-callable wrapper around
 * the suspend `serveDriverHost`, so a non-coroutine caller (a Java `main`/test) can host a driver.
 * [close] stops serving and removes the socket. (A small step toward a native `dev.jumpstarter.*`
 * surface over the raw `dev.jumpstarter.core.*` bindings.)
 */
class DriverHostServer private constructor(
    private val scope: CoroutineScope,
    private val socketPath: String,
) : AutoCloseable {
    /** The UDS path the host is bound to (export as `JUMPSTARTER_HOST` for an in-process client). */
    val host: String get() = socketPath

    override fun close() {
        scope.cancel()
        File(socketPath).delete()
    }

    companion object {
        /** Start serving `factory` on `udsPath`, blocking until the socket is bound (or timeout). */
        @JvmStatic
        @JvmOverloads
        fun serve(
            udsPath: String,
            factory: DriverHostFactory,
            timeoutMillis: Long = 10_000,
        ): DriverHostServer {
            val scope = CoroutineScope(Dispatchers.IO + SupervisorJob())
            scope.launch { serveDriverHost(udsPath, factory) }
            val socket = File(udsPath)
            val deadlineNanos = System.nanoTime() + timeoutMillis * 1_000_000
            while (!socket.exists() && System.nanoTime() < deadlineNanos) {
                Thread.sleep(25)
            }
            check(socket.exists()) { "serveDriverHost never bound the socket at $udsPath" }
            return DriverHostServer(scope, udsPath)
        }
    }
}
