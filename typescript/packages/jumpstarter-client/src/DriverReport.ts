/**
 * Represents an exporter's device tree as returned by `ExporterService.GetReport`.
 */

/** A single driver instance in the device tree. */
export interface DriverInstance {
  /** Unique ID of this driver instance within the exporter. */
  readonly uuid: string;
  /** Parent driver UUID, or undefined for root-level drivers. */
  readonly parentUuid?: string;
  /** Labels including `jumpstarter.dev/name` and `jumpstarter.dev/client`. */
  readonly labels: ReadonlyMap<string, string>;
  /** Human-readable description, or undefined. */
  readonly description?: string;
  /** Map of method name to help text. */
  readonly methodsDescription: ReadonlyMap<string, string>;
  /** Fully-qualified gRPC service names supported by this driver. */
  readonly nativeServices: readonly string[];
}

/** The full device tree report from an exporter. */
export class DriverReport {
  /** The exporter's root UUID. */
  readonly uuid: string;
  /** The exporter's labels. */
  readonly labels: ReadonlyMap<string, string>;
  /** All driver instances in the device tree. */
  readonly instances: readonly DriverInstance[];

  constructor(response: {
    uuid: string;
    labelsMap: Array<[string, string]>;
    reportsList: Array<{
      uuid: string;
      parentUuid?: string;
      labelsMap: Array<[string, string]>;
      description?: string;
      methodsDescriptionMap: Array<[string, string]>;
      nativeServicesList: string[];
    }>;
  }) {
    this.uuid = response.uuid;
    this.labels = new Map(response.labelsMap);
    this.instances = response.reportsList.map((r) => ({
      uuid: r.uuid,
      parentUuid: r.parentUuid || undefined,
      labels: new Map(r.labelsMap),
      description: r.description || undefined,
      methodsDescription: new Map(r.methodsDescriptionMap),
      nativeServices: r.nativeServicesList,
    }));
  }

  /**
   * Find a driver instance by its `jumpstarter.dev/name` label.
   *
   * @param name - The driver name
   * @returns The matching instance, or undefined if not found
   */
  findByName(name: string): DriverInstance | undefined {
    return this.instances.find(
      (inst) => inst.labels.get("jumpstarter.dev/name") === name,
    );
  }

  /**
   * Find a driver instance by its `jumpstarter.dev/name` label, throwing
   * if not found.
   *
   * @param name - The driver name
   * @returns The matching instance
   * @throws Error if no driver with this name exists
   */
  requireByName(name: string): DriverInstance {
    const inst = this.findByName(name);
    if (!inst) {
      const available = this.instances
        .map((i) => i.labels.get("jumpstarter.dev/name") ?? i.uuid)
        .join(", ");
      throw new Error(
        `No driver found with name: ${name}. Available: [${available}]`,
      );
    }
    return inst;
  }
}
