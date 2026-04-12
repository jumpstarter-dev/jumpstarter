package dev.jumpstarter.client;

import jumpstarter.v1.Jumpstarter;
import org.jetbrains.annotations.NotNull;
import org.jetbrains.annotations.Nullable;

import java.util.ArrayList;
import java.util.Collections;
import java.util.List;
import java.util.Map;

/**
 * Represents an exporter's device tree as returned by {@code ExporterService.GetReport}.
 */
public final class DriverReport {

    private final String uuid;
    private final Map<String, String> labels;
    private final List<DriverInstance> instances;

    DriverReport(@NotNull Jumpstarter.GetReportResponse response) {
        this.uuid = response.getUuid();
        this.labels = Collections.unmodifiableMap(response.getLabelsMap());
        List<DriverInstance> list = new ArrayList<>();
        for (Jumpstarter.DriverInstanceReport r : response.getReportsList()) {
            list.add(new DriverInstance(r));
        }
        this.instances = Collections.unmodifiableList(list);
    }

    /** The exporter's root UUID. */
    @NotNull
    public String getUuid() {
        return uuid;
    }

    /** The exporter's labels. */
    @NotNull
    public Map<String, String> getLabels() {
        return labels;
    }

    /** All driver instances in the device tree. */
    @NotNull
    public List<DriverInstance> getInstances() {
        return instances;
    }

    /**
     * Find a driver instance by its {@code jumpstarter.dev/name} label.
     *
     * @param name the driver name
     * @return the matching instance, or null if not found
     */
    @Nullable
    public DriverInstance findByName(@NotNull String name) {
        for (DriverInstance instance : instances) {
            String n = instance.getLabels().get("jumpstarter.dev/name");
            if (name.equals(n)) {
                return instance;
            }
        }
        return null;
    }

    /**
     * Represents a single driver instance in the device tree.
     */
    public static final class DriverInstance {
        private final String uuid;
        private final String parentUuid;
        private final Map<String, String> labels;
        private final String description;
        private final Map<String, String> methodsDescription;

        DriverInstance(@NotNull Jumpstarter.DriverInstanceReport report) {
            this.uuid = report.getUuid();
            this.parentUuid = report.hasParentUuid() ? report.getParentUuid() : null;
            this.labels = Collections.unmodifiableMap(report.getLabelsMap());
            this.description = report.hasDescription() ? report.getDescription() : null;
            this.methodsDescription = Collections.unmodifiableMap(report.getMethodsDescriptionMap());
        }

        /** Unique ID of this driver instance within the exporter. */
        @NotNull
        public String getUuid() {
            return uuid;
        }

        /** Parent driver UUID, or null for root-level drivers. */
        @Nullable
        public String getParentUuid() {
            return parentUuid;
        }

        /** Labels including {@code jumpstarter.dev/name} and {@code jumpstarter.dev/client}. */
        @NotNull
        public Map<String, String> getLabels() {
            return labels;
        }

        /** Human-readable description, or null. */
        @Nullable
        public String getDescription() {
            return description;
        }

        /** Map of method name to help text. */
        @NotNull
        public Map<String, String> getMethodsDescription() {
            return methodsDescription;
        }

        @Override
        public String toString() {
            String name = labels.getOrDefault("jumpstarter.dev/name", uuid);
            return "DriverInstance{name=" + name + ", uuid=" + uuid + "}";
        }
    }
}
