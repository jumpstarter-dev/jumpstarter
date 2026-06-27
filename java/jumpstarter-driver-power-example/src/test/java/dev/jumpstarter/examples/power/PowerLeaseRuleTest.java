package dev.jumpstarter.examples.power;

import static org.junit.Assert.assertTrue;

import dev.jumpstarter.generated.power.PowerClient;
import dev.jumpstarter.testing.JumpstarterLease;
import dev.jumpstarter.testing.JumpstarterRule;
import jumpstarter.interfaces.power.v1.Power.PowerReading;
import java.util.List;
import org.junit.ClassRule;
import org.junit.Test;

/**
 * Example — the <b>JUnit 4</b> (AOSP 14 / Tradefed) way to drive the power driver through a LEASED
 * exporter. The {@link JumpstarterLease} annotation specifies the selector exactly as for JUnit 5/6;
 * the {@link JumpstarterRule} reads it, acquires the lease, and exposes the session, which builds the
 * generated {@link PowerClient}.
 *
 * <p>Tradefed runs this with its own JUnit 4 runner. Our Gradle build compiles it but does not
 * execute it (there is no JUnit Vintage engine on the test runtime), so it never tries to acquire a
 * lease in CI.
 */
@JumpstarterLease(selector = "example.com/board=power")
public class PowerLeaseRuleTest {
    @ClassRule public static final JumpstarterRule jmp = new JumpstarterRule();

    @Test
    public void powerOnOffThroughLease() {
        PowerClient power = new PowerClient(jmp.getSession(), "power");

        power.on();
        List<PowerReading> on = power.read();
        assertTrue(on.stream().allMatch(r -> r.getVoltage() > 0.0));

        power.off();
        List<PowerReading> off = power.read();
        assertTrue(off.stream().allMatch(r -> r.getVoltage() == 0.0));
    }
}
