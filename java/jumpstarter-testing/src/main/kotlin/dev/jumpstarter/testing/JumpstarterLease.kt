package dev.jumpstarter.testing

/**
 * Declares the lease a test class wants — the uniform way to specify the exporter `selector` (and
 * lease `alias`/`durationSecs`) across **all** JUnit generations. Read by the JUnit 5/6
 * [JumpstarterExtension] and the JUnit 4 [JumpstarterRule] alike.
 *
 * ```kotlin
 * @Jumpstarter(selector = "board=rpi4")
 * @ExtendWith(JumpstarterExtension::class)   // JUnit 5/6
 * class PowerTest { @Test fun t(session: ExporterSession) { … } }
 * ```
 * ```java
 * @Jumpstarter(selector = "board=rpi4")       // JUnit 4 (AOSP/Tradefed)
 * public class PowerTest {
 *     @ClassRule public static final JumpstarterRule jmp = new JumpstarterRule();
 * }
 * ```
 *
 * When `JUMPSTARTER_HOST` is set (running inside a `jmp shell`), `selector` is ignored — the shell
 * already chose the exporter. An empty `selector` means "any matching exporter".
 */
@Target(AnnotationTarget.CLASS)
@Retention(AnnotationRetention.RUNTIME)
annotation class JumpstarterLease(
    val selector: String = "",
    val alias: String = "default",
    val durationSecs: Long = 1800,
)
