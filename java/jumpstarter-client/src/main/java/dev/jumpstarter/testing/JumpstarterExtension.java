package dev.jumpstarter.testing;

import dev.jumpstarter.client.ExporterSession;
import org.junit.jupiter.api.extension.*;

import java.lang.reflect.Constructor;
import java.lang.reflect.Field;
import java.util.logging.Logger;

/**
 * JUnit 5 extension that manages an {@link ExporterSession} lifecycle and
 * injects typed device wrappers into fields annotated with {@link JumpstarterDevice}.
 *
 * <p>The extension reads {@code JUMPSTARTER_HOST} from the environment (set by
 * {@code jmp shell}), creates an {@link ExporterSession} before all tests,
 * and closes it after all tests complete.
 *
 * <p>Fields annotated with {@link JumpstarterDevice} are injected before each
 * test. The field type must have a constructor that accepts a single
 * {@link ExporterSession} argument.
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
public class JumpstarterExtension
        implements BeforeAllCallback, AfterAllCallback, TestInstancePostProcessor {

    private static final Logger logger = Logger.getLogger(JumpstarterExtension.class.getName());
    private static final ExtensionContext.Namespace NAMESPACE =
            ExtensionContext.Namespace.create(JumpstarterExtension.class);
    private static final String SESSION_KEY = "exporter-session";

    @Override
    public void beforeAll(ExtensionContext context) {
        ExporterSession session = ExporterSession.fromEnv();
        context.getStore(NAMESPACE).put(SESSION_KEY, session);
        logger.info("Jumpstarter session established with " +
                session.getReport().getInstances().size() + " driver(s)");
    }

    @Override
    public void afterAll(ExtensionContext context) {
        ExporterSession session = context.getStore(NAMESPACE)
                .remove(SESSION_KEY, ExporterSession.class);
        if (session != null) {
            session.close();
            logger.info("Jumpstarter session closed");
        }
    }

    @Override
    public void postProcessTestInstance(Object testInstance, ExtensionContext context)
            throws Exception {
        ExporterSession session = context.getStore(NAMESPACE)
                .get(SESSION_KEY, ExporterSession.class);
        if (session == null) {
            // Walk up to find the session in parent contexts (for nested test classes)
            ExtensionContext parent = context.getParent().orElse(null);
            while (parent != null && session == null) {
                session = parent.getStore(NAMESPACE).get(SESSION_KEY, ExporterSession.class);
                parent = parent.getParent().orElse(null);
            }
        }

        if (session == null) {
            throw new ExtensionConfigurationException(
                    "No ExporterSession found. Ensure @ExtendWith(JumpstarterExtension.class) "
                            + "is on the test class.");
        }

        for (Field field : testInstance.getClass().getDeclaredFields()) {
            if (field.isAnnotationPresent(JumpstarterDevice.class)) {
                injectDevice(field, testInstance, session);
            }
        }
    }

    private void injectDevice(Field field, Object testInstance, ExporterSession session)
            throws Exception {
        Class<?> deviceType = field.getType();

        Constructor<?> ctor;
        try {
            ctor = deviceType.getConstructor(ExporterSession.class);
        } catch (NoSuchMethodException e) {
            throw new ExtensionConfigurationException(
                    "Device type " + deviceType.getName() + " must have a public constructor "
                            + "accepting ExporterSession. "
                            + "Is this a generated device wrapper class?");
        }

        Object device = ctor.newInstance(session);
        field.setAccessible(true);
        field.set(testInstance, device);
    }
}
