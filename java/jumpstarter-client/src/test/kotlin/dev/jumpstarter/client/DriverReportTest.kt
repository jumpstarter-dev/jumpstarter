package dev.jumpstarter.client

import org.junit.jupiter.api.Assertions.assertEquals
import org.junit.jupiter.api.Assertions.assertNull
import org.junit.jupiter.api.Assertions.assertThrows
import org.junit.jupiter.api.Test

/** Pure unit tests for the GetReport JSON parsing — no FFI / exporter needed. */
class DriverReportTest {
    @Test
    fun parsesNodesAndLooksUpByName() {
        val json = """
            [
              {"uuid":"u-power","parent_uuid":null,"labels":{"jumpstarter.dev/name":"power"}},
              {"uuid":"u-serial","parent_uuid":"u-power","labels":{"jumpstarter.dev/name":"serial"}}
            ]
        """.trimIndent()

        val report = DriverReport.parse(json)

        assertEquals(2, report.instances.size)
        assertEquals("u-power", report.requireByName("power").uuid)
        assertEquals("serial", report.findByName("serial")?.name)
        assertEquals("u-power", report.findByName("serial")?.parentUuid)
        assertNull(report.findByName("missing"))
    }

    @Test
    fun requireByNameThrowsForMissingDriver() {
        val report = DriverReport.parse("[]")
        assertThrows(NoSuchElementException::class.java) { report.requireByName("nope") }
    }

    /** Two same-named `power` nodes under different composites + an unnamed synthetic root. */
    private fun nestedReport(): DriverReport = DriverReport.parse(
        """
            [
              {"uuid":"root","parent_uuid":null,"labels":{}},
              {"uuid":"dut-a","parent_uuid":"root","labels":{"jumpstarter.dev/name":"dut_a"}},
              {"uuid":"dut-b","parent_uuid":"root","labels":{"jumpstarter.dev/name":"dut_b"}},
              {"uuid":"pow-a","parent_uuid":"dut-a","labels":{"jumpstarter.dev/name":"power"}},
              {"uuid":"pow-b","parent_uuid":"dut-b","labels":{"jumpstarter.dev/name":"power"}},
              {"uuid":"solo","parent_uuid":null,"labels":{"jumpstarter.dev/name":"solo_power"}}
            ]
        """.trimIndent(),
    )

    @Test
    fun findByPathResolvesNestedAndDisambiguatesDuplicates() {
        val report = nestedReport()
        assertEquals("pow-a", report.requireByPath("dut_a", "power").uuid)
        assertEquals("pow-b", report.requireByPath("dut_b", "power").uuid)
        assertEquals("solo", report.requireByPath("solo_power").uuid)
        // First segment may be a child of the unnamed synthetic root.
        assertEquals("dut-a", report.requireByPath("dut_a").uuid)
        // Deeply nested names do not leak to the root fallback.
        assertNull(report.findByPath("power"))
        assertNull(report.findByPath("dut_a", "nope"))
        assertThrows(NoSuchElementException::class.java) { report.requireByPath("dut_a", "nope") }
    }

    @Test
    fun findByPathThrowsWhenFirstSegmentIsAmbiguous() {
        // Two federated roots, each exporting a `power` entry.
        val report = DriverReport.parse(
            """
                [
                  {"uuid":"r1","parent_uuid":null,"labels":{}},
                  {"uuid":"r2","parent_uuid":null,"labels":{}},
                  {"uuid":"p1","parent_uuid":"r1","labels":{"jumpstarter.dev/name":"power"}},
                  {"uuid":"p2","parent_uuid":"r2","labels":{"jumpstarter.dev/name":"power"}}
                ]
            """.trimIndent(),
        )
        assertThrows(IllegalStateException::class.java) { report.findByPath("power") }
    }
}
