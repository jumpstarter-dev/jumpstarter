package dev.jumpstarter.driver

/**
 * Marks a proto-first JVM driver (a stock grpc-java `…ImplBase` / grpc-kotlin `…CoroutineImplBase`
 * service) so it can be hosted with NO hand-written factory: the reflective host instantiates the
 * class, derives its descriptor from the stock service, and reads [client] for the
 * `jumpstarter.dev/client` label. The same annotation works in Kotlin and Java. Use it with
 * [GrpcServiceDriverHostFactory.forDriver] (tests) or the config-driven host (the `type:` is the
 * class FQN). Replaces writing a `…DriverHostFactory`:
 *
 * ```
 * @JumpstarterDriver(client = "jumpstarter_driver_power.client.PowerClient")
 * class KotlinPowerDriver : PowerInterfaceCoroutineImplBase() { … }
 * ```
 */
@Target(AnnotationTarget.CLASS)
@Retention(AnnotationRetention.RUNTIME)
annotation class JumpstarterDriver(
    /** The `jumpstarter.dev/client` label — the default client that drives this driver. */
    val client: String,
)
