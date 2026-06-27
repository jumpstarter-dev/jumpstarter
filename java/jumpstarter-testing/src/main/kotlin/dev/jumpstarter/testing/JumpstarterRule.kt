package dev.jumpstarter.testing

import dev.jumpstarter.client.ExporterSession
import org.junit.rules.ExternalResource

/**
 * A JUnit 4 `@Rule`/`@ClassRule` providing a leased Jumpstarter session — the JUnit 4 (AOSP 14 /
 * Tradefed) counterpart of the JUnit 5/6 [JumpstarterExtension]. Both adapters share the
 * harness-agnostic [JumpstarterLease], so the lease/session behaviour is identical across JUnit
 * generations.
 *
 * Acquires the lease in `before` (connecting to an existing `jmp shell` lease via `JUMPSTARTER_HOST`
 * if set, else autonomously via the Rust core's `LeasedExporter`) and releases it in `after`. Prefer
 * a `@ClassRule` so one lease spans the class:
 *
 * ```java
 * public class PowerTest {
 *     @ClassRule public static final JumpstarterRule jmp = new JumpstarterRule();
 *
 *     @Test public void powersOn() {
 *         new PowerClient(jmp.getSession(), "power").on();
 *     }
 * }
 * ```
 *
 * Pass a custom [provider] to control selector / alias / duration, e.g.
 * `new JumpstarterRule(() -> JumpstarterLease.acquire("board=rpi4", "default", 1800))`.
 */
class JumpstarterRule @JvmOverloads constructor(
    private val provider: () -> JumpstarterLease = { JumpstarterLease.fromEnvironmentOrAcquire() },
) : ExternalResource() {
    private var lease: JumpstarterLease? = null

    /** The connected session — valid for the rule's active scope; build clients from it. */
    val session: ExporterSession
        get() = (lease ?: error("JumpstarterRule has not started (use it as a @Rule/@ClassRule)")).session

    override fun before() {
        lease = provider()
    }

    override fun after() {
        lease?.close()
        lease = null
    }
}
