package dev.jumpstarter.client.integration;

import dev.jumpstarter.client.DriverClient;
import dev.jumpstarter.client.DriverReport;
import dev.jumpstarter.client.ExporterSession;
import org.junit.jupiter.api.AfterAll;
import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.Tag;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.condition.EnabledIfEnvironmentVariable;

import java.util.Iterator;
import java.util.List;

import static org.junit.jupiter.api.Assertions.*;

/**
 * Integration tests that run inside a {@code jmp shell} session.
 *
 * <h3>How to run:</h3>
 * <pre>
 * # Start the shell with the example exporter config:
 * jmp shell --exporter-config java/examples/exporter.yaml
 *
 * # Inside the shell, run the integration tests:
 * cd java && ./gradlew integrationTest
 * </pre>
 *
 * <p>These tests are skipped when {@code JUMPSTARTER_HOST} is not set
 * (i.e. when not running inside a jmp shell).
 */
@Tag("integration")
@EnabledIfEnvironmentVariable(named = "JUMPSTARTER_HOST", matches = ".+")
class JmpShellIntegrationTest {

    private static ExporterSession session;

    @BeforeAll
    static void connect() {
        session = ExporterSession.fromEnv();
    }

    @AfterAll
    static void disconnect() {
        if (session != null) {
            session.close();
        }
    }

    @Test
    void getReport() {
        DriverReport report = session.getReport();

        assertNotNull(report.getUuid());
        assertFalse(report.getInstances().isEmpty(), "Expected at least one driver instance");

        // Print the device tree
        System.out.println("Exporter UUID: " + report.getUuid());
        System.out.println("Labels: " + report.getLabels());
        for (DriverReport.DriverInstance inst : report.getInstances()) {
            System.out.println("  " + inst);
            System.out.println("    Methods: " + inst.getMethodsDescription().keySet());
        }
    }

    @Test
    void findDriversByName() {
        DriverReport report = session.getReport();

        // The example exporter has "power" and "shell" drivers
        DriverReport.DriverInstance power = report.findByName("power");
        assertNotNull(power, "Expected 'power' driver in report");

        DriverReport.DriverInstance shell = report.findByName("shell");
        assertNotNull(shell, "Expected 'shell' driver in report");
    }

    @Test
    void mockPowerOnOff() {
        DriverClient power = session.driverClientByName("power");

        // Turn on
        Object onResult = power.call("on");
        System.out.println("power.on() -> " + onResult);

        // Turn off
        Object offResult = power.call("off");
        System.out.println("power.off() -> " + offResult);
    }

    @Test
    void mockPowerRead() {
        DriverClient power = session.driverClientByName("power");

        // Read returns a stream of PowerReading (voltage, current)
        Iterator<Object> readings = power.streamingCall("read");
        assertTrue(readings.hasNext(), "Expected at least one power reading");

        Object reading = readings.next();
        System.out.println("Power reading: " + reading);
        assertNotNull(reading);
    }

    @Test
    void shellGetMethods() {
        DriverClient shell = session.driverClientByName("shell");

        // get_methods returns the list of configured shell commands
        Object result = shell.call("get_methods");
        System.out.println("Shell methods: " + result);

        assertNotNull(result);
        assertInstanceOf(List.class, result);

        @SuppressWarnings("unchecked")
        List<Object> methods = (List<Object>) result;
        assertTrue(methods.contains("hello"), "Expected 'hello' method");
        assertTrue(methods.contains("hostname"), "Expected 'hostname' method");
        assertTrue(methods.contains("date"), "Expected 'date' method");
    }

    @Test
    void shellCallHello() {
        DriverClient shell = session.driverClientByName("shell");

        // call_method is a streaming call: yields (stdout, stderr, exit_code) tuples
        // The last item has the exit code
        Iterator<Object> results = shell.streamingCall("call_method", "hello", null);

        StringBuilder stdout = new StringBuilder();
        while (results.hasNext()) {
            Object chunk = results.next();
            System.out.println("Shell output chunk: " + chunk);
            if (chunk instanceof List<?> tuple && tuple.size() >= 1) {
                Object out = tuple.get(0);
                if (out instanceof String s) {
                    stdout.append(s);
                }
            }
        }

        assertTrue(stdout.toString().contains("Hello from Jumpstarter"),
                "Expected 'Hello from Jumpstarter' in output, got: " + stdout);
    }

    @Test
    void shellCallDate() {
        DriverClient shell = session.driverClientByName("shell");

        Iterator<Object> results = shell.streamingCall("call_method", "date", null);

        StringBuilder stdout = new StringBuilder();
        while (results.hasNext()) {
            Object result = results.next();
            if (result instanceof List<?> tuple && tuple.size() >= 1) {
                Object out = tuple.get(0);
                if (out instanceof String s) {
                    stdout.append(s);
                }
            }
        }

        // date +%s returns a Unix timestamp (all digits)
        String output = stdout.toString().trim();
        System.out.println("Date output: " + output);
        assertFalse(output.isEmpty(), "Expected date output");
    }
}
