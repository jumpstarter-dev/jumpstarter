package dev.jumpstarter.testing

import dev.jumpstarter.client.ExporterSession
import org.junit.jupiter.api.extension.AfterAllCallback
import org.junit.jupiter.api.extension.BeforeAllCallback
import org.junit.jupiter.api.extension.ExtensionContext
import org.junit.jupiter.api.extension.ParameterContext
import org.junit.jupiter.api.extension.ParameterResolver

/**
 * A JUnit 5 extension that provides a leased Jumpstarter session to a test class — the JVM analog of
 * Python's pytest `jumpstarter-testing` fixtures and Rust's `#[jumpstarter_test]`.
 *
 * On `@BeforeAll` it obtains a [Lease] (connecting to the lease an outer `jmp shell`
 * already holds when `JUMPSTARTER_HOST` is set, else autonomously acquiring one via the Rust core's
 * `LeasedExporter`), and releases it on `@AfterAll`. A test method — or the test constructor — may
 * take a [Lease] or an [ExporterSession] parameter; build typed clients from the session:
 *
 * ```
 * @ExtendWith(JumpstarterExtension::class)
 * class PowerTest {
 *     @Test fun powersOn(session: ExporterSession) { PowerClient(session, "power").on() }
 * }
 * ```
 *
 * The framework is JUnit-agnostic at its core ([Lease]), so the same lease/session flow
 * drives any harness (Tradefed, a plain `main`, …).
 */
class JumpstarterExtension : BeforeAllCallback, AfterAllCallback, ParameterResolver {
    override fun beforeAll(context: ExtensionContext) {
        // Honour a `@JumpstarterLease(selector = …)` on the test class (the same annotation the JUnit 4
        // rule reads), so the lease selector is specified the same way across JUnit generations.
        val cfg = context.requiredTestClass.getAnnotation(JumpstarterLease::class.java)
        val lease = Lease.fromEnvironmentOrAcquire(
            cfg?.selector?.takeIf { it.isNotEmpty() },
            cfg?.alias ?: "default",
            cfg?.durationSecs ?: 1800,
        )
        store(context).put(LEASE_KEY, lease)
    }

    override fun afterAll(context: ExtensionContext) {
        (store(context).get(LEASE_KEY) as? Lease)?.close()
    }

    override fun supportsParameter(pc: ParameterContext, ec: ExtensionContext): Boolean =
        pc.parameter.type == Lease::class.java ||
            pc.parameter.type == ExporterSession::class.java

    override fun resolveParameter(pc: ParameterContext, ec: ExtensionContext): Any {
        val lease = store(ec).get(LEASE_KEY) as Lease
        return if (pc.parameter.type == Lease::class.java) lease else lease.session
    }

    private fun store(context: ExtensionContext) =
        context.getStore(ExtensionContext.Namespace.create(JumpstarterExtension::class.java))

    private companion object {
        const val LEASE_KEY = "jumpstarter.lease"
    }
}
