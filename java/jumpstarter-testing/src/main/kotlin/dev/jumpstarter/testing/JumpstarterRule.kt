package dev.jumpstarter.testing

import dev.jumpstarter.client.ExporterSession
import org.junit.rules.TestRule
import org.junit.runner.Description
import org.junit.runners.model.Statement

/**
 * A JUnit 4 `@Rule`/`@ClassRule` providing a leased Jumpstarter session — the JUnit 4 (AOSP 14 /
 * Tradefed) counterpart of the JUnit 5/6 [JumpstarterExtension]. Both adapters share the
 * harness-agnostic [Lease], so behaviour is identical across JUnit generations.
 *
 * The selector (and alias/duration) is specified the same way as for JUnit 5/6 — a [JumpstarterLease]
 * annotation on the test class, which this rule reads from the JUnit `Description`:
 *
 * ```java
 * @JumpstarterLease(selector = "board=rpi4")
 * public class PowerTest {
 *     @ClassRule public static final JumpstarterRule jmp = new JumpstarterRule();
 *     @Test public void powersOn() { new PowerClient(jmp.getSession(), "power").on(); }
 * }
 * ```
 *
 * The selector can also be passed explicitly — `new JumpstarterRule("board=rpi4")` — or a fully
 * custom lease supplied via the functional constructor. Acquires in setup (connecting to an existing
 * `jmp shell` lease via `JUMPSTARTER_HOST` if set, else autonomously via the Rust core's
 * `LeasedExporter`) and releases in teardown.
 */
class JumpstarterRule private constructor(
    private val leaseFor: (Description) -> Lease,
) : TestRule {
    private var lease: Lease? = null

    /** Read the lease config from the test class's [JumpstarterLease] annotation (or `JUMPSTARTER_HOST`). */
    constructor() : this({ description ->
        val cfg = description.testClass?.getAnnotation(JumpstarterLease::class.java)
        Lease.fromEnvironmentOrAcquire(
            cfg?.selector?.takeIf { it.isNotEmpty() },
            cfg?.alias ?: "default",
            cfg?.durationSecs ?: 1800,
        )
    })

    /** Explicit lease config (overrides any [JumpstarterLease] annotation). */
    @JvmOverloads
    constructor(selector: String, alias: String = "default", durationSecs: Long = 1800) :
        this({ _: Description -> Lease.fromEnvironmentOrAcquire(selector, alias, durationSecs) })

    /** A fully custom lease provider. */
    constructor(provider: () -> Lease) : this({ _: Description -> provider() })

    /** The connected session — valid for the rule's active scope; build clients from it. */
    val session: ExporterSession
        get() = (lease ?: error("JumpstarterRule has not started (use it as a @Rule/@ClassRule)")).session

    override fun apply(base: Statement, description: Description): Statement = object : Statement() {
        override fun evaluate() {
            lease = leaseFor(description)
            try {
                base.evaluate()
            } finally {
                lease?.close()
                lease = null
            }
        }
    }
}
