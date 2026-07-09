package dev.jumpstarter.examples.power

import dev.jumpstarter.driver.ConfigDrivenHostFactory
import kotlinx.coroutines.runBlocking
import org.junit.jupiter.api.Assertions.assertEquals
import org.junit.jupiter.api.Test

/**
 * The config-driven (reflective) host must advertise the driver's real client label.
 *
 * Regression: it used to ignore `@JumpstarterDriver(client = …)` and synthesize
 * `jumpstarter_driver_<name>.client.<Name>Client` from the entry name — a class that does not
 * exist — so a `type: jvm:…` driver reached the client tree as an unloadable stub and its `j`
 * command vanished, even though the driver itself served calls fine.
 */
class ConfigDrivenHostFactoryTest {
    private fun describeLabels(yaml: String): Map<String, String> = runBlocking {
        val node = ConfigDrivenHostFactory.fromYaml(yaml).newHost().describe().single()
        node.labels
    }

    @Test
    fun annotationClientLabelIsAdvertised() {
        val labels = describeLabels(
            """
            export:
              jvmpower:
                type: jvm:dev.jumpstarter.examples.power.KotlinPowerDriver
            """.trimIndent(),
        )
        assertEquals("jvmpower", labels["jumpstarter.dev/name"])
        // From @JumpstarterDriver(client = …) on KotlinPowerDriver — NOT the synthesized
        // jumpstarter_driver_jvmpower.client.JvmpowerClient.
        assertEquals("jumpstarter_driver_power.client.PowerClient", labels["jumpstarter.dev/client"])
    }

    @Test
    fun explicitConfigClientOverridesAnnotation() {
        val labels = describeLabels(
            """
            export:
              jvmpower:
                type: jvm:dev.jumpstarter.examples.power.KotlinPowerDriver
                client: my.custom.Client
            """.trimIndent(),
        )
        assertEquals("my.custom.Client", labels["jumpstarter.dev/client"])
    }
}
