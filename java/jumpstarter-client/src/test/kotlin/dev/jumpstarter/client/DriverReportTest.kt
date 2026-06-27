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
}
