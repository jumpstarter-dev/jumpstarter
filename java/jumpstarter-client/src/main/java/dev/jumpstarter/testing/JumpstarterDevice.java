package dev.jumpstarter.testing;

import java.lang.annotation.ElementType;
import java.lang.annotation.Retention;
import java.lang.annotation.RetentionPolicy;
import java.lang.annotation.Target;

/**
 * Marks a field for injection of a typed device wrapper by {@link JumpstarterExtension}.
 *
 * <p>The annotated field's type must be a generated device wrapper class
 * (e.g. {@code DevBoardDevice}) that has a single-argument constructor
 * accepting {@link dev.jumpstarter.client.ExporterSession}.
 *
 * <p>Usage:
 * <pre>{@code
 * @ExtendWith(JumpstarterExtension.class)
 * class PowerTest {
 *     @JumpstarterDevice
 *     DevBoardDevice device;
 *
 *     @Test
 *     void powerOn() {
 *         device.power().on();
 *     }
 * }
 * }</pre>
 */
@Target(ElementType.FIELD)
@Retention(RetentionPolicy.RUNTIME)
public @interface JumpstarterDevice {
}
