# JEP-0002: ExporterClass Mechanism

| Field             | Value                                                      |
| ----------------- | ---------------------------------------------------------- |
| **JEP**           | 0002                                                       |
| **Title**         | ExporterClass Mechanism                                    |
| **Author(s)**     | @kirkbrauer (Kirk Brauer)                                  |
| **Status**        | Draft                                                      |
| **Type**          | Standards Track                                            |
| **Created**       | 2026-04-06                                                 |
| **Updated**       | 2026-04-08                                                 |
| **Discussion**    | [Matrix](https://matrix.to/#/#jumpstarter:matrix.org)      |
| **Requires**      | JEP-0001 (Protobuf Introspection and Interface Generation) |
| **Supersedes**    | —                                                          |
| **Superseded-By** | —                                                          |

---

## Abstract

This JEP introduces an `ExporterClass` custom resource that defines a typed contract between device providers (exporters) and device consumers (test clients). An ExporterClass specifies required and optional driver interfaces by referencing `DriverInterface` CRDs, enabling the controller to structurally validate exporters at registration time using the `FileDescriptorProto` schemas from JEP-0001, and enabling client-side codegen (JEP-0003) to produce type-safe device wrappers with named accessors for each interface. An accompanying `DriverInterface` CRD links interface names to their canonical proto definitions and driver packages. Together, these resources bridge the gap between label-based infrastructure selection and typed API contracts.

## Motivation

Today, a client requesting a device lease specifies label selectors — `soc=sa8295p`, `vendor=acme` — and gets an exporter with matching metadata. But labels describe *infrastructure* (what hardware is connected), not *API contracts* (what driver interfaces are available). A client that receives a lease has no guarantee about which driver interfaces the exporter provides. It must call `GetReport`, walk the driver tree, check for the presence of each interface by name, and handle missing interfaces at runtime. This makes it impossible to write type-safe client code in any language.

The consequences are concrete:

- **No compile-time safety.** A test that needs `power.on()`, `serial.connect()`, and `flash.write()` cannot know at compile time whether the leased device provides all three. If the serial driver is missing, the test discovers this at runtime — potentially minutes into a CI pipeline — with an opaque `KeyError` or `AttributeError`.

- **No contract for exporters.** An operator deploying a new exporter for embedded device testing has no way to verify that the exporter's driver configuration satisfies the requirements of the test suites that will lease it. Misconfiguration is discovered when tests fail, not at deployment time.

- **No typed codegen.** Without a formal declaration of which interfaces a device provides, code generation tools (JEP-0003) cannot produce typed device wrappers with non-nullable accessors. Every interface accessor must be `Optional`, defeating the purpose of typed clients.

- **No fleet-level visibility.** There is no aggregated view of how many exporters satisfy a particular device profile. An operator cannot answer "how many embedded-linux-board devices are available right now?" without scripting custom `GetReport` queries across the fleet.

ExporterClass bridges the gap between infrastructure selection (labels) and API contracts (interfaces), following the established Kubernetes pattern used by `StorageClass`, `IngressClass`, and `RuntimeClass`.

### User Stories

- **As a** CI pipeline author writing tests for an embedded Linux board, **I want** the controller to enforce that leased exporters provide the power, serial, and storage interfaces my tests need, **so that** my pipeline gets a device guaranteed to have the correct driver interfaces — or fails immediately if none is available.

- **As a** platform operator deploying a new exporter rack, **I want to** validate my exporter configuration against the `embedded-linux-board` ExporterClass before connecting it to the controller, **so that** I catch missing drivers or interface mismatches at deployment time rather than when a test fails at 2 AM.

- **As a** test framework developer generating typed clients, **I want** the ExporterClass definition to tell me exactly which interfaces are required vs. optional, **so that** I can generate non-nullable accessors for required interfaces and `Optional` accessors for optional ones.

- **As a** fleet manager responsible for 50+ exporters across three labs, **I want to** see at a glance how many exporters satisfy each ExporterClass and which ones are missing interfaces, **so that** I can plan capacity and prioritize hardware procurement.

- **As a** driver developer adding network support to an existing exporter, **I want to** check whether my updated exporter configuration now satisfies the `embedded-linux-board` ExporterClass (which lists network as optional), **so that** I can verify my work without running the full test suite.

## Proposal

### Overview

This proposal introduces two new Kubernetes CRDs and admin CLI tooling:

1. **`DriverInterface`** — a namespace-scoped CRD that names an interface, references its canonical proto definition (from JEP-0001), and specifies how to identify drivers implementing it in a `DriverInstanceReport`. DriverInterface CRDs for all bundled drivers ship as part of the standard Jumpstarter installation.
2. **`ExporterClass`** — a namespace-scoped CRD that declares a named device profile as a set of required and optional `DriverInterface` references, plus label selectors.
3. **`jmp admin` CLI tooling** — commands for managing `DriverInterface` and `ExporterClass` CRDs, generating DriverInterface YAML for custom drivers, and validating exporter configurations locally.

ExporterClass is a **purely admin-side enforcement mechanism** that requires no changes to the lease protocol or client behavior. Clients continue to request leases using labels exactly as they do today. The controller evaluates exporters against ExporterClasses based on the ExporterClass's label selector: when an exporter's labels match an ExporterClass's selector, the controller validates that the exporter also satisfies the ExporterClass's interface requirements. Exporters that match the labels but fail interface validation are flagged and excluded from lease matching for that label set, ensuring that clients always receive exporters with the correct driver interfaces. Client-side codegen (JEP-0003) can consume ExporterClass definitions to produce typed device wrappers.

### DriverInterface CRD

A `DriverInterface` is a lightweight registry entry that gives a stable name to a driver interface and links it to its canonical proto definition:

```yaml
apiVersion: jumpstarter.dev/v1alpha1
kind: DriverInterface
metadata:
  # Fully-qualified name derived from the proto package
  name: dev-jumpstarter-power-v1
  namespace: lab-detroit
spec:
  # Proto definition — canonical identifier and descriptor for this interface
  proto:
    # The proto package name — used for matching drivers to interfaces
    package: jumpstarter.interfaces.power.v1
    # Canonical FileDescriptorProto bytes (base64-encoded)
    # Generated by: jmp admin generate driverinterface <interface-class>
    descriptor: CpoBMQpwanVtcHN0YXJ0ZXIvaW50ZXJmYWNlcy9wb3dlci92MS...

  # Driver implementations for this interface, per language
  # Driver implementations for this interface, per language
  drivers:
    - language: python
      package: jumpstarter-driver-power
      version: "1.0.0"
      index: https://pkg.jumpstarter.dev/
      clientClass: jumpstarter_driver_power.client:PowerClient
      # Optional — multiple implementations may exist (e.g., MockPower, DutlinkPower)
      driverClasses:
        - jumpstarter_driver_power.driver:MockPower
        - jumpstarter_driver_power.driver:DutlinkPower

status:
  implementationCount: 15
  conditions:
    - type: Ready
      status: "True"
```

The DriverInterface CRD name is a fully-qualified identifier derived from the proto package (e.g., `dev-jumpstarter-power-v1` for `jumpstarter.interfaces.power.v1`), ensuring uniqueness within a namespace.

The `proto.package` field contains the proto package name (e.g., `jumpstarter.interfaces.power.v1`) — this is the canonical identifier used for matching. JEP-0001's `build_file_descriptor()` produces `FileDescriptorProto` objects with this same package name. The controller matches a driver in the report tree to a DriverInterface by comparing the driver's `FileDescriptorProto.package` against the DriverInterface's `proto.package`. This eliminates the need for convention-based label matching — the proto package is the canonical identifier.

The `descriptor` field contains the canonical `FileDescriptorProto` as base64-encoded bytes, used by the controller for structural validation. This follows the pattern established by Envoy's gRPC-JSON transcoder, which embeds `FileDescriptorSet` bytes inline in Kubernetes configuration.

The `drivers` array lists driver implementations for this interface, one per language. Each entry specifies the language (`lang`), package name, version constraint, package index, and the language-specific class paths. For Python, `driverClass` is the driver implementation and `clientClass` is the client proxy class (e.g., `jumpstarter_driver_power.client:PowerClient`), matching the `jumpstarter.dev/client` label already emitted by drivers in their `DriverInstanceReport`. This enables client-side validation (verifying the correct packages are installed) and provides the import paths for codegen (JEP-0003). Future language support (e.g., Java, Kotlin, Go) adds entries to the same array.

### ExporterClass CRD

An `ExporterClass` declares a device profile as a set of selectors and interface requirements:

```yaml
apiVersion: jumpstarter.dev/v1alpha1
kind: ExporterClass
metadata:
  name: embedded-linux-board
  namespace: lab-detroit
spec:
  # Standard Kubernetes label selectors for exporter matching.
  # Each selector must be satisfied by an exporter to match this ExporterClass.
  selector:
    matchLabels:
      device-type: embedded-linux
    matchExpressions:
      - key: arch
        operator: In
        values: [arm64, amd64]

  # Interface requirements — validated against DriverInstanceReport using DriverInterface matching.
  interfaces:
    - name: power               # accessor name in generated client
      interfaceRef: dev-jumpstarter-power-v1    # references a DriverInterface by name
      required: true
    - name: serial
      interfaceRef: dev-jumpstarter-serial-v1
      required: true
    - name: storage
      interfaceRef: dev-jumpstarter-storage-v1
      required: true
    - name: network
      interfaceRef: dev-jumpstarter-network-v1
      required: false

status:
  satisfiedExporterCount: 12
  resolvedInterfaces: [dev-jumpstarter-power-v1, dev-jumpstarter-serial-v1, dev-jumpstarter-storage-v1, dev-jumpstarter-network-v1]
  conditions:
    - type: Ready
      status: "True"
      reason: "ExportersSatisfied"
      message: "12 exporters satisfy all required interfaces"
```

The `spec` has two sections:

**`selector`** — standard Kubernetes label selectors (`matchLabels` and `matchExpressions`) that each candidate exporter must satisfy. This uses the same label selector mechanism already used by the lease controller, making it familiar to operators and requiring no additional dependencies.

**`interfaces`** — a Jumpstarter-specific extension. Each entry has three fields: `name` (the accessor name used in generated client code — e.g., `device.power`, `device.serial`), `interfaceRef` (a reference to a `DriverInterface` by name), and `required` (whether the interface must be present for an exporter to satisfy this ExporterClass). This section is what makes ExporterClass a *typed API contract* rather than just a selector — it declares which driver interfaces the device must provide, enabling codegen and structural validation.

ExporterClass is purely about typing and selection — it does not include driver-specific configuration. Driver parameters (e.g., power cycle delay, serial baud rate) belong in the exporter's `ExporterConfig` YAML, not in the ExporterClass contract.

### ExporterClass Inheritance

ExporterClasses can extend other ExporterClasses to create specialization hierarchies:

```yaml
apiVersion: jumpstarter.dev/v1alpha1
kind: ExporterClass
metadata:
  name: base-device
  namespace: lab-detroit
spec:
  selector:
    matchLabels:
      managed-by: jumpstarter
  interfaces:
    - name: power
      interfaceRef: dev-jumpstarter-power-v1
      required: true
    - name: serial
      interfaceRef: dev-jumpstarter-serial-v1
      required: true
---
apiVersion: jumpstarter.dev/v1alpha1
kind: ExporterClass
metadata:
  name: embedded-linux-board
  namespace: lab-detroit
spec:
  extends: base-device  # inherits selector + power + serial
  selector:
    matchLabels:
      device-type: embedded-linux
  interfaces:
    - name: storage
      interfaceRef: dev-jumpstarter-storage-v1
      required: true
    - name: network
      interfaceRef: dev-jumpstarter-network-v1
      required: false
```

Tests written against `base-device` run on any managed target (any device with power and serial). Tests written against `embedded-linux-board` require storage in addition to the base power + serial. An exporter that satisfies `embedded-linux-board` automatically satisfies `base-device` — the inheritance is structural, not declared on the exporter.

The `extends` chain is resolved at ExporterClass reconciliation time, not at lease time. The flattened interface list is cached in `status.resolvedInterfaces`. Circular `extends` chains are detected during reconciliation and result in a `Degraded` condition.

### Schema Distribution and CLI Workflow

A key operational question is how `DriverInterface` and `ExporterClass` CRDs are authored, distributed, and applied to clusters. This JEP defines a distribution model that minimizes operator friction by shipping DriverInterface CRDs as part of the standard Jumpstarter installation.

#### DriverInterface distribution

DriverInterface CRDs are treated as **part of the Jumpstarter platform**, versioned and distributed alongside the operator and bundled drivers:

**Bundled drivers** (power, serial, network, ADB, storage, etc.):
- DriverInterface YAML manifests are generated as part of the standard `make manifests` build process and placed alongside the existing CRDs in:
  - Helm chart: `deploy/helm/jumpstarter/charts/jumpstarter-controller/templates/crds/`
  - Operator config: `deploy/operator/config/crd/bases/`
  - OLM bundle: included automatically via `operator-sdk generate bundle`
- The `proto.descriptor` for each DriverInterface is generated at build time by invoking `jmp admin generate driverinterface` (or an equivalent build script) against each bundled driver's Python interface class, producing the serialized `FileDescriptorProto` from JEP-0001's `build_file_descriptor()`
- A new `make driver-interfaces` Makefile target orchestrates this generation. It first runs `jmp proto export` (per interface) (from JEP-0001) to ensure all `.proto` files for bundled drivers are up-to-date, then discovers all bundled `@driverinterface`-decorated classes via the `jumpstarter.drivers` entry point group, generates a DriverInterface YAML for each (with `descriptor` derived from the freshly-generated proto), and outputs them to the CRD directories. This target is called by `make manifests` so that DriverInterface YAMLs are always in sync with the driver code
- When Jumpstarter is upgraded, DriverInterface CRDs are updated alongside the operator to reflect any interface changes in the new version

**Third-party/custom drivers:**
- Custom driver packages ship their own DriverInterface YAML (e.g., under `k8s/driverinterface.yaml`)
- Administrators generate the YAML using `jmp admin generate driverinterface` and apply it with `jmp admin apply driverinterface`

This approach ensures that installing Jumpstarter automatically provides the DriverInterface CRDs for all bundled drivers, making it seamless for administrators to start using ExporterClasses without manual schema publishing.

#### Admin CLI commands

All cluster management operations for DriverInterface and ExporterClass live under the `jmp admin` subcommand, following the kubectl-style `verb noun` pattern:

```bash
# List DriverInterface CRDs in the current namespace
jmp admin get driverinterfaces

# Apply a DriverInterface CRD to the cluster (for custom drivers)
jmp admin apply driverinterface custom-driver-interface.yaml

# Generate DriverInterface YAML from an installed custom driver
jmp admin generate driverinterface jumpstarter_driver_custom.driver.CustomInterface > custom-v1.yaml

# Apply an ExporterClass to the cluster
jmp admin apply exporterclass embedded-linux-board.yaml

# List ExporterClasses in the current namespace
jmp admin get exporterclasses
```

Exporter validation is a user-facing command under the top-level `jmp` CLI:

```bash
# Validate an exporter config against all matching ExporterClasses
jmp validate exporter /etc/jumpstarter/exporters/my-exporter.yaml
```

The command loads the exporter configuration, introspects its driver tree to build `DriverInstanceReport` data, and calls the `ValidateExporter` RPC on the controller. The controller resolves which ExporterClasses match the exporter's labels and validates the interface requirements, returning the results. This works through the existing Jumpstarter controller API using the exporter's credentials — no direct Kubernetes cluster access is required.

Client-side validation is also available:

```bash
# Validate that installed client packages match the server's DriverInterface definitions
jmp validate client
```

The `jmp validate client` command calls the `GetExporterClassInfo` RPC using the client's credentials, then checks the locally installed driver client packages against the `drivers` entries reported by the server. For each DriverInterface, it verifies:
- The required Python package is installed (e.g., `jumpstarter-driver-power`)
- The installed version matches the version declared in the DriverInterface
- The `clientClass` is importable

This catches mismatches before a test runs — e.g., a CI environment missing a required driver package, or running a stale version that doesn't match the server's DriverInterface definitions.

#### Workflow

1. **Install Jumpstarter** — the operator installation includes DriverInterface CRDs for all bundled drivers. No additional steps needed for standard interfaces.
2. **(Optional) Custom drivers** — for third-party drivers, run `jmp admin generate driverinterface` to produce the DriverInterface YAML, then `jmp admin apply driverinterface` to register it.
3. **Author ExporterClass** — a lab admin writes an ExporterClass YAML referencing the installed DriverInterfaces by name.
4. **Apply ExporterClass** — `jmp admin apply exporterclass` or `kubectl apply` registers the ExporterClass.
5. **Validate** — the controller validates exporters against ExporterClasses at registration time. Operators can also pre-validate with `jmp validate exporter <path>`, which calls the controller's `ValidateExporter` RPC using the exporter's existing credentials.

### Controller Validation Flow

#### On exporter registration (`Register` RPC)

Registration is **never rejected** due to ExporterClass non-compliance — the exporter is always accepted and its `DriverInstanceReport` tree is stored in `ExporterStatus.Devices`. This ensures backward compatibility: exporters that predate ExporterClass continue to register and serve label-only leases without disruption.

After storing the device reports, the controller evaluates the exporter against all ExporterClasses in the namespace:

1. Receive exporter's labels and `DriverInstanceReport` tree (which now includes `file_descriptor_proto` from JEP-0001).
2. For each `ExporterClass` in the namespace:
   a. Evaluate the label selector against the exporter's labels using standard Kubernetes `labels.Selector` matching. All selector criteria must be satisfied.
   b. For each `required: true` interface entry, resolve the referenced `DriverInterface` and verify a driver in the report tree has a `FileDescriptorProto` whose package name matches the DriverInterface's `proto.package` (e.g., `jumpstarter.interfaces.power.v1`).
   c. When a matching driver is found, compare its `file_descriptor_proto` against the `DriverInterface`'s `proto.descriptor` for structural validation — verifying that method names, parameter types, return types, and streaming semantics are compatible.
3. Tag the exporter with satisfied ExporterClasses (stored in `ExporterStatus.SatisfiedExporterClasses`).
4. For ExporterClasses where the exporter's labels match but interface validation fails (missing interfaces, structural mismatches), record a **validation failure condition** on the Exporter status with details:
   - Which ExporterClass was evaluated
   - Which interfaces are missing or structurally incompatible
   - What specific methods or types are mismatched (for structural failures)
5. Update each ExporterClass's `status.satisfiedExporterCount`.

This "accept and flag" approach means:
- Exporters always register successfully — no disruption to existing workflows
- Administrators see clear feedback on the Exporter resource about which ExporterClasses are satisfied and which are not (and why)
- The exporter remains available for label-only leases even if it fails ExporterClass validation
- `kubectl describe exporter <name>` shows compliance status at a glance

Example condition on a non-compliant exporter:

```yaml
status:
  satisfiedExporterClasses: []
  conditions:
    - type: ExporterClassCompliance
      status: "False"
      reason: "InterfaceMismatch"
      message: >-
        ExporterClass 'embedded-linux-board': missing required interface 'serial-v1';
        interface 'power-v1' structurally incompatible (missing method 'read',
        expected server_streaming rpc)
```

#### On lease request (with ExporterClass enforcement)

The lease protocol is unchanged — clients request leases with label selectors as before. The ExporterClass enforcement is transparent to the client:

1. Receive the lease request with its `LabelSelector`.
2. Find candidate exporters matching the label selector.
3. For each candidate, check whether any ExporterClass in the namespace has a selector that matches the exporter's labels. If so, verify the exporter appears in that ExporterClass's `SatisfiedExporterClasses` — i.e., the exporter has been validated as compliant. Exclude non-compliant exporters from candidacy.
4. Bind lease to the best available compliant exporter.

If all label-matching exporters are excluded due to ExporterClass non-compliance, the lease request fails with a descriptive error indicating which interfaces are missing or structurally incompatible, referencing the `ExporterClassCompliance` conditions on the failed exporters. This gives the client (and the CI pipeline operator) actionable information about why no exporter was available.

### Local Validation Tool

```bash
# Validate an exporter config against all matching ExporterClasses
jmp validate exporter /etc/jumpstarter/exporters/my-exporter.yaml
```

This loads the exporter configuration, introspects the driver classes without full hardware initialization, and calls the controller's `ValidateExporter` RPC to check against all matching ExporterClasses. Output indicates which requirements are satisfied, which are missing, and which are optional:

```text
ExporterClass: embedded-linux-board
  ✓ power      (power-v1)                   → DutlinkPower
  ✓ adb        (adb-v1)                     → AdbDevice
  ✓ flash      (storage-mux-flasher-v1)     → DutlinkStorageMux
  ✓ serial     (serial-v1)                  → PySerial
  ○ can        (can-v1alpha1)  [optional]   → not found

Result: SATISFIED (4/4 required, 0/1 optional)
```

This provides fast feedback during driver development and exporter deployment — an operator can verify compliance before connecting the exporter to the controller.

### Structural Validation with `FileDescriptorProto`

JEP-0001 embeds a `FileDescriptorProto` in each driver's `DriverInstanceReport`. ExporterClass validation uses a two-stage process: **package matching** followed by **structural comparison**.

**Stage 1 — Package matching:** The controller identifies which interface a driver implements by parsing its `FileDescriptorProto.package` field. JEP-0001's `build_file_descriptor()` produces packages like `jumpstarter.interfaces.power.v1`. The controller matches this against the DriverInterface's `proto.package`. This is the primary identification mechanism — no convention-based labels are needed.

**Stage 2 — Structural comparison:** Once a driver is matched to a DriverInterface by package, the controller compares the two `FileDescriptorProto` descriptors:

1. The `DriverInterface` CRD's `proto.descriptor` contains the canonical `FileDescriptorProto` — the reference schema for that interface at that version.
2. The exporter's driver report contains the driver's `FileDescriptorProto` — the schema that the driver actually implements.
3. The controller (or `jmp validate exporter`) compares the two descriptors to verify structural compatibility: are all required methods present? Do their parameter types, return types, and streaming semantics match?

For the Experimental phase, structural validation uses **moderate strictness**: method names must exist, input/output message field types must match, and streaming semantics must match. Field number comparison is deferred to the Stable phase, since JEP-0001's builder generates deterministic field numbers that may differ from hand-written protos.

This moves validation from convention-based to structure-based — the descriptor proves the driver has `on()`, `off()`, and `read()` with correct signatures. A driver that claims to implement `power-v1` but is missing the `read()` streaming method, or has changed `off()` to require a parameter, is caught at registration time — not when a test fails.

Exporters that don't embed `FileDescriptorProto` (pre-JEP-0001 deployments) cannot be validated against ExporterClasses and are treated as non-compliant for ExporterClass matching purposes. They remain fully functional for label-only leases.

### API / Protocol Changes

#### New CRDs

Two new namespace-scoped CRDs are added to the Jumpstarter operator:

- `driverinterfaces.jumpstarter.dev/v1alpha1` — `DriverInterface`
- `exporterclasses.jumpstarter.dev/v1alpha1` — `ExporterClass`

Both CRDs are namespace-scoped to support multi-tenant lab environments where different teams manage their own device profiles and interface definitions within their namespace. This diverges from the Kubernetes `*Class` convention (where `StorageClass`, `IngressClass`, etc. are cluster-scoped) but better fits Jumpstarter's deployment model, where multiple labs or teams share a single cluster with independent device profiles.

Both are additive — they don't modify existing CRDs (`Exporter`, `Client`, `Lease`).

#### Modified CRD

The `ExporterStatus` struct gains new fields:

```go
type ExporterStatus struct {
    // ... existing fields (Conditions, Devices, LeaseRef, etc.) ...

    // ExporterClasses that this exporter satisfies (all required interfaces present and valid)
    SatisfiedExporterClasses []string `json:"satisfiedExporterClasses,omitempty"`
}
```

The existing `Conditions` slice on `ExporterStatus` is used for the `ExporterClassCompliance` condition type, which reports validation failures. This avoids runtime re-evaluation of ExporterClass satisfaction at lease time — the controller consults `SatisfiedExporterClasses` directly.

#### New RPC

A `ValidateExporter` RPC is added to `ControllerService`, allowing exporter operators to pre-validate their configuration against matching ExporterClasses without requiring direct Kubernetes cluster access:

```protobuf
rpc ValidateExporter(ValidateExporterRequest) returns (ValidateExporterResponse);

message ValidateExporterRequest {
  map<string, string> labels = 1;
  repeated DriverInstanceReport reports = 2;
}

message ValidateExporterResponse {
  repeated ExporterClassValidationResult results = 1;
}

message ExporterClassValidationResult {
  string exporter_class_name = 1;
  bool satisfied = 2;
  repeated InterfaceValidationResult interfaces = 3;
}

message InterfaceValidationResult {
  string interface_name = 1;
  string interface_ref = 2;
  bool required = 3;
  bool found = 4;
  bool structurally_compatible = 5;
  string error_message = 6;  // empty if valid
}
```

The RPC authenticates using the exporter's existing credentials (the same token used for `Register`). The controller resolves which ExporterClasses match the provided labels and validates the driver reports against each one, returning per-interface results.

A `GetExporterClassInfo` RPC is also added, callable by clients to retrieve the ExporterClass and DriverInterface definitions that apply to a leased exporter. This enables clients to discover which interfaces are available, check for missing client packages, and provides the foundation for future codegen (JEP-0003):

```protobuf
rpc GetExporterClassInfo(GetExporterClassInfoRequest) returns (GetExporterClassInfoResponse);

message GetExporterClassInfoRequest {
  string exporter_uuid = 1;
}

message GetExporterClassInfoResponse {
  repeated ExporterClassValidationResult exporter_classes = 1;
  repeated DriverInterfaceInfo driver_interfaces = 2;
}

message DriverInterfaceInfo {
  string name = 1;                 // e.g., "dev-jumpstarter-power-v1"
  string package = 2;              // proto package, e.g., "jumpstarter.interfaces.power.v1"
  bytes descriptor = 3;            // canonical FileDescriptorProto
  repeated DriverInfo drivers = 4;
}

message DriverInfo {
  string lang = 1;                 // e.g., "python", "java"
  string package = 2;              // e.g., "jumpstarter-driver-power"
  string version = 3;              // e.g., "1.0.0"
  string index = 4;                // e.g., "https://pkg.jumpstarter.dev/"
  string client_class = 5;         // e.g., "jumpstarter_driver_power.client:PowerClient"
  repeated string driver_classes = 6;  // optional, e.g., ["...driver:MockPower", "...driver:DutlinkPower"]
}
```

The RPC authenticates using the client's credentials. It returns the ExporterClass compliance status and the full DriverInterface metadata for the exporter, allowing clients to verify they have the correct driver packages installed and to feed the interface definitions into codegen tooling. The detailed design of client-side codegen is deferred to JEP-0003.

#### Modified CLI

New admin subcommands are added following the kubectl-style `verb noun` pattern: `jmp admin get driverinterfaces`, `jmp admin apply driverinterface`, `jmp admin generate driverinterface`, `jmp admin get exporterclasses`, `jmp admin apply exporterclass`. New user-facing commands `jmp validate exporter <path>` (exporter validation) and `jmp validate client` (client package validation) are added. No existing CLI commands are modified.

### Hardware Considerations

This JEP is a control-plane change. No hardware is required or affected. ExporterClass and DriverInterface are Kubernetes CRDs processed by the controller. The validation logic operates on `DriverInstanceReport` metadata and `FileDescriptorProto` descriptors — it does not interact with physical devices or timing-sensitive operations.

The `jmp validate` tool loads exporter configurations and introspects driver classes but does not initialize hardware. It runs on the operator's workstation, not on the exporter host.

## Design Details

### Architecture

```text
┌────────────────────────┐     ┌────────────────────────┐
│   DriverInterface CRD  │     │   DriverInterface CRD  │
│  dev-jumpstarter-      │     │  dev-jumpstarter-      │
│    power-v1            │     │    serial-v1           │
└───────────┬────────────┘     └───────────┬────────────┘
            │                              │
            └──────────┬───────────────────┘
                       │  referenced by
                       ▼
            ┌──────────────────────┐
            │  ExporterClass CRD   │
            │   embedded-linux-board   │
            │   interfaces:        │
            │     power: dev-j..-v1│
            │     serial: dev-j..-v1
            │     ...              │
            └──────────┬───────────┘
                       │  validated against
                       ▼
            ┌──────────────────────┐
            │   Exporter           │
            │   labels + report    │
            │   ┌────────────────┐ │
            │   │DriverInstance- │ │
            │   │Report tree     │ │
            │   │  ├─ power (fd) │ │
            │   │  ├─ serial (fd)│ │
            │   │  └─ ...        │ │
            │   └────────────────┘ │
            └──────────────────────┘
```

### Data Flow

1. **Installation:** The Jumpstarter operator installation includes DriverInterface CRDs for all bundled drivers. For custom drivers, `jmp admin generate driverinterface` produces the DriverInterface YAML, and `jmp admin apply driverinterface` registers it.
2. **ExporterClass creation:** A lab admin applies `ExporterClass` YAML manifests to the cluster via `jmp admin apply exporterclass`, referencing DriverInterface CRDs by name.
3. **Exporter registration:** An exporter calls `Register` with its labels and `DriverInstanceReport` tree. The controller evaluates the exporter against all ExporterClasses in the namespace and tags it with the ones it satisfies.
4. **Lease request:** A client requests a lease with label selectors as usual. The controller filters candidates to exporters that satisfy the relevant ExporterClass (if any ExporterClass's selector matches the exporter's labels) and binds the lease.
5. **ExporterClass status:** The controller maintains `status.satisfiedExporterCount` on each ExporterClass, updated on exporter registration, deregistration, and configuration changes.

### Resolved Design Decisions

#### CRD Scope: Namespace-scoped

ExporterClass and DriverInterface are namespace-scoped to support multi-tenant lab environments. Different teams can define their own device profiles and interface definitions within their namespace. Lease matching already operates within namespaces in the existing controller. While this diverges from the K8s `*Class` convention (StorageClass, IngressClass are cluster-scoped), it better fits Jumpstarter's multi-lab deployment model where teams need independent control over their device profiles.

#### Selectors: Standard Kubernetes Label Selectors

ExporterClass uses standard `matchLabels`/`matchExpressions` selectors instead of CEL expressions. This eliminates the need to add a direct CEL dependency (though `cel-go` is available as an indirect dependency via `k8s.io/apiserver`), matches the existing pattern in the lease controller (which already uses `labels.Selector`), and is simpler for operators familiar with standard Kubernetes label selectors. CEL-based selectors can be added as a future enhancement if more expressive power is needed.

#### Structural Validation Strictness: Moderate for Experimental

During the Experimental phase, structural validation compares: method names (all canonical methods must exist in the driver descriptor), input/output message field types (field names and proto types must match), and streaming semantics (server_streaming, client_streaming flags must match). Field number comparison is deferred to the Stable phase, since JEP-0001's `build_file_descriptor()` generates deterministic field numbers that may differ from hand-written proto files.

#### Canonical Descriptor Storage: Inline in CRD

The `DriverInterface` CRD stores the canonical `FileDescriptorProto` as base64-encoded bytes directly in `proto.descriptor`, alongside the proto package name in `proto.package`. This follows the pattern established by Envoy's gRPC-JSON transcoder, which embeds `FileDescriptorSet` bytes inline in Kubernetes configuration. A future JEP-0004 (Driver Registry) can add registry-based resolution as an alternative source.

### Error Handling and Failure Modes

- **Non-compliant exporter registration:** Registration always succeeds. If an exporter's driver report is missing required interfaces or has structurally incompatible descriptors, the exporter is accepted but flagged with an `ExporterClassCompliance` condition detailing the failures. The exporter remains available for label-only leases. This ensures that introducing ExporterClasses never breaks existing exporter registration flows.
- **Missing DriverInterface:** If an ExporterClass references an `interfaceRef` that doesn't exist as a DriverInterface CRD, the ExporterClass enters a `Degraded` condition with a descriptive message. Exporters are not validated against the missing interface.
- **No compliant exporters for lease:** If all exporters matching a lease request's labels are excluded due to ExporterClass non-compliance, the controller returns an immediate error with details: which ExporterClass applied, which interfaces are missing or incompatible across the fleet, and the specific validation failures from the `ExporterClassCompliance` conditions.
- **Structural validation failure:** When a driver's `FileDescriptorProto` is present but structurally incompatible with the `DriverInterface`'s `proto.descriptor` (missing methods, type mismatches, wrong streaming semantics), the specific mismatches are recorded in the exporter's compliance condition. The exporter is not tagged as satisfying the ExporterClass, but it is not rejected from the cluster.
- **Circular inheritance:** If ExporterClass `extends` creates a cycle (A extends B extends A), the controller detects this at reconciliation time and sets a `Degraded` condition with a descriptive error.
- **Stale exporter tags:** If an ExporterClass is modified (e.g., a new required interface is added), the controller re-evaluates all tagged exporters, removes tags from those that no longer satisfy the updated requirements, and updates their `ExporterClassCompliance` conditions accordingly.

### Concurrency and Thread-Safety

ExporterClass and DriverInterface are standard Kubernetes CRDs, managed by the controller's reconciliation loop. The controller uses Kubernetes informers with watch/list semantics — standard patterns for handling concurrent updates. Exporter tagging uses status subresources with optimistic concurrency (resourceVersion-based conflict detection). The ExporterClass reconciler watches Exporter resources (via `Watches()` in `SetupWithManager`) to update `satisfiedExporterCount` when exporters register or deregister.

### Security Implications

ExporterClass and DriverInterface CRDs are namespace-scoped resources, subject to standard Kubernetes RBAC. Only users with appropriate roles can create, modify, or delete them within their namespace. The `jmp validate` tool runs locally and does not require cluster access — it reads ExporterClass YAML files from the filesystem.

The structural validation against `FileDescriptorProto` uses the same authenticated `GetReport` data that the controller already receives during exporter registration. No additional authentication or transport security is required.

## Feasibility Assessment

### JEP-0001 PoC Readiness

The JEP-0001 PoC (commit `b40abc06`) provides a solid foundation for this JEP. The following capabilities are already implemented and ready:

| Capability                                                        | Status |
| ----------------------------------------------------------------- | ------ |
| `FileDescriptorProto` in `DriverInstanceReport` (end-to-end flow) | Ready  |
| `@driverinterface(name, version)` decorator                       | Ready  |
| `build_file_descriptor()` producing canonical descriptors         | Ready  |
| `DriverInterfaceMeta._registry` interface registry                | Ready  |
| `Device.FileDescriptorProto []byte` in Go controller types        | Ready  |
| gRPC Server Reflection                                            | Ready  |
| Driver entry point registration (`jumpstarter.drivers` group)     | Ready  |
| `jmp driver list` discovery via `importlib.metadata`              | Ready  |
| `jmp proto export` producing .proto from Python interfaces  | Ready  |
| Proto files shipping with driver packages                         | Ready  |

### Gaps Requiring Implementation

1. **FileDescriptorProto parsing in Go controller**: The `Device.FileDescriptorProto` bytes are stored opaquely. Go-side deserialization using `google.golang.org/protobuf/types/descriptorpb` (already in dependencies) is needed for structural validation.

2. **ExporterClass/DriverInterface CRDs**: Two new namespace-scoped CRDs following existing kubebuilder patterns.

3. **CLI tooling**: `jmp admin get/apply/generate driverinterface`, `jmp admin get/apply exporterclass`, `jmp validate exporter` commands.

4. **Build integration**: A new `make driver-interfaces` Makefile target must be added to `controller/Makefile`. This target first runs `jmp proto export` (per interface) to ensure all bundled driver `.proto` files are up-to-date, then generates DriverInterface YAMLs with `proto.descriptor` from `build_file_descriptor()` and outputs them alongside the CRDs. This target should be invoked by the existing `make manifests` target.

## Test Plan

### Unit Tests

- **DriverInterface matching:** Verify that the controller correctly identifies drivers in a `DriverInstanceReport` tree by matching the `FileDescriptorProto.package` against the DriverInterface's `proto.package`.
- **ExporterClass evaluation:** Verify that an exporter is correctly tagged as satisfying an ExporterClass when all required interfaces are present in the report tree, and not tagged when any required interface is missing.
- **Optional interface handling:** Verify that missing optional interfaces do not prevent an exporter from satisfying an ExporterClass.
- **ExporterClass inheritance:** Verify that an `extends` chain correctly merges interface requirements from parent and child ExporterClasses.
- **Circular inheritance detection:** Verify that a circular `extends` chain is detected and results in a `Degraded` condition.
- **Structural validation:** Verify that `FileDescriptorProto` comparison catches method mismatches (missing methods, type differences, streaming semantics changes) between a driver's descriptor and the DriverInterface's canonical descriptor.
- **Package-based matching:** Verify that the controller correctly matches a driver's `FileDescriptorProto.package` (e.g., `jumpstarter.interfaces.power.v1`) against the DriverInterface's `proto.package`.
- **Label selector evaluation:** Verify that `matchLabels` and `matchExpressions` correctly filter exporters by their labels, and that all selector criteria must pass for a match.
- **Selector merging:** Verify that the controller correctly applies ExporterClass label selectors alongside the lease request's `selector`.

### Integration Tests

- **End-to-end lease with ExporterClass enforcement:** Apply DriverInterface and ExporterClass CRDs, register a compliant exporter and a non-compliant exporter with the same labels, request a lease with those labels, and verify the lease binds only to the compliant exporter.
- **Non-compliant exporter exclusion:** Register an exporter missing a required interface, request a lease matching its labels, and verify the exporter is excluded with a descriptive error referencing the `ExporterClassCompliance` condition.
- **`jmp validate exporter` end-to-end:** Run `jmp validate exporter` against an exporter configuration, verify it automatically resolves matching ExporterClasses from the cluster and produces correct pass/fail output.
- **`jmp admin generate driverinterface` end-to-end:** Run `jmp admin generate driverinterface` for a custom driver package and verify the DriverInterface YAML is produced with correct `proto.descriptor`.
- **ExporterClass status:** Register multiple exporters, verify `status.satisfiedExporterCount` is accurate, deregister one, verify the count decrements.
- **CRD update re-evaluation:** Modify an ExporterClass to add a new required interface, verify exporters are re-evaluated and tags are updated.

### Hardware-in-the-Loop Tests

No HiL tests are required for this JEP. ExporterClass is a control-plane feature operating on CRDs and `DriverInstanceReport` metadata. The `jmp validate` tool introspects driver classes without initializing hardware.

### Manual Verification

- Apply the `embedded-linux-board` ExporterClass and associated DriverInterfaces to a test cluster. Register exporters with varying driver configurations and verify the ExporterClass `status` reflects the correct count.
- Request a lease with labels matching the `embedded-linux-board` ExporterClass selector and verify only compliant exporters are selected.
- Run `jmp validate exporter` against a real exporter configuration and verify the output accurately reflects the exporter's interface compliance.
- Verify that DriverInterface CRDs for bundled drivers are correctly included in the operator installation.

## Graduation Criteria

### Experimental

- DriverInterface and ExporterClass CRDs are installable on a Jumpstarter operator deployment.
- Controller validates exporters against ExporterClasses at registration time using package-based matching.
- Lease requests with labels matching an ExporterClass selector correctly exclude non-compliant exporters.
- `jmp validate exporter` produces correct output for at least three exporter configurations.
- DriverInterface CRDs for all bundled drivers are included in the operator Helm chart/OLM bundle.
- `jmp admin generate driverinterface` produces correct DriverInterface YAML for custom drivers.
- At least one ExporterClass (`embedded-linux-board` or `embedded-linux`) is defined and used in the project's CI.

### Stable

- Structural validation using `FileDescriptorProto` comparison is implemented and enabled by default (moderate strictness: method names, type structure, streaming semantics).
- ExporterClass inheritance (`extends`) is implemented and tested.
- `status.satisfiedExporterCount` is maintained accurately across exporter lifecycle events.
- At least one downstream JEP (Codegen or Registry) consumes ExporterClass definitions.
- Interface version evolution strategy is documented and tested.
- No breaking changes to the CRD schema for at least one release cycle.
- Field number comparison added to structural validation (strict mode).

## Backward Compatibility

This JEP is **fully backward compatible.** All changes are additive:

- No changes to the lease protocol or client-facing CLI. Clients continue to request leases with label selectors exactly as before. ExporterClass enforcement is transparent to clients.

- The `ExporterClass` and `DriverInterface` CRDs are new resources. They don't modify existing CRDs (`Exporter`, `Client`, `Lease`). Clusters without these CRDs installed behave exactly as before.

- Exporter registration is unchanged. The controller's additional ExporterClass evaluation is a read-only check that tags exporters with satisfied ExporterClasses. Exporters that predate ExporterClass are simply not tagged — they remain available for label-only leases.

- Exporters that don't embed `FileDescriptorProto` (pre-JEP-0001 deployments) are not affected by ExporterClass enforcement — they cannot be validated and are treated as non-compliant for ExporterClass purposes, but remain fully functional for label-only leases.

- The `jmp admin` subcommands (`get/apply/generate driverinterface`, `get/apply exporterclass`, `jmp validate exporter`) are new. No existing CLI commands are modified.

- DriverInterface CRDs for bundled drivers are included in the operator installation. Upgrading Jumpstarter automatically updates these CRDs. Existing clusters without DriverInterface/ExporterClass CRDs continue to function exactly as before — the feature is fully opt-in.

## Rejected Alternatives

### Embedding interface requirements in labels

An early approach considered encoding interface requirements as exporter labels (e.g., `jumpstarter.dev/has-power=true`, `jumpstarter.dev/has-serial=true`) and matching them with standard label selectors. This was rejected because labels are unstructured strings with no validation — they can't express versioning, optional vs. required semantics, or structural compatibility. They also pollute the label space and require manual synchronization between exporter configuration and label values.

### Using annotations instead of CRDs

An alternative considered storing ExporterClass definitions as annotations on a shared ConfigMap. This was rejected because annotations have a 256 KB size limit, lack schema validation, don't support status subresources, and don't integrate with Kubernetes RBAC or the controller's informer/watch infrastructure.

### Defining ExporterClass as a gRPC-only API (no CRD)

An alternative considered defining ExporterClass as a gRPC service on the controller (like the Registry in JEP-0004) rather than a Kubernetes CRD. This was rejected because CRDs provide declarative management via `kubectl apply`, RBAC integration, status subresources, and watch semantics for free — all of which a gRPC API would need to reimplement. ExporterClasses are cluster configuration, not runtime data; CRDs are the natural Kubernetes primitive for this.

### Requiring all interfaces to be required (no optional)

A simpler model considered making all interfaces in an ExporterClass required, with no optional flag. This was rejected because real device profiles have varying capabilities — network access is available on some embedded boards but not all, and tests should be able to target the common denominator (`base-device`) or the full profile (`embedded-linux-board`) as appropriate.

### Cluster-scoped CRDs

The Kubernetes `*Class` convention (StorageClass, IngressClass, RuntimeClass) uses cluster-scoped resources. This was considered but rejected for Jumpstarter because namespace-scoping better supports multi-tenant lab environments where different teams need independent control over their device profiles and interface definitions. Lease matching already operates within namespaces, and namespace-scoping provides natural RBAC isolation without additional configuration.

## Prior Art

- **Kubernetes DRA DeviceClass** (`resource.k8s.io/v1`) — a design influence. Jumpstarter's ExporterClass aligns with the K8s DRA `DeviceClass` in several ways: named CRD, selectors, and the overall pattern of a named class that defines selection criteria for devices. Where Jumpstarter diverges is the `interfaces` section — K8s DRA selects devices by attributes and capacity (e.g., GPU memory, driver name), while Jumpstarter selects exporters by the driver interfaces they provide (e.g., power, serial, ADB). The ExporterClass also uses standard label selectors instead of K8s DRA's CEL-based selectors, is namespace-scoped instead of cluster-scoped, and deliberately excludes driver-specific configuration (which belongs in the exporter's `ExporterConfig`). K8s DRA uses `ResourceClaim`/`ResourceClaimTemplate` for allocation; Jumpstarter uses its own Lease mechanism.

- **Kubernetes StorageClass / IngressClass / RuntimeClass** — Kubernetes uses the `*Class` pattern extensively to abstract infrastructure profiles into named contracts. `StorageClass` maps a name to a storage provisioner with parameters; `IngressClass` maps a name to an ingress controller. Jumpstarter's ExporterClass follows the same naming convention.

- **LAVA device types** (Linaro Automated Validation Architecture) — LAVA uses device type definitions (Jinja2 templates) to describe hardware capabilities and select compatible test jobs. Jumpstarter's ExporterClass is more strongly typed (label selectors + proto-based structural validation vs. YAML templates) but serves the same matching purpose in HiL testing.

- **OpenAPI / Swagger schemas** — OpenAPI defines API contracts that are validated at request time. ExporterClass performs an analogous validation at the infrastructure level — verifying that a device provides the API contract that test code expects.

- **Buf Schema Registry (BSR)** — Buf handles proto module versioning and breaking change detection via structural comparison of `FileDescriptorProto` at the descriptor level. The `buf breaking` command's WIRE category rules (checking for removed RPCs, changed field types/numbers, streaming semantics changes) are directly applicable to DriverInterface structural validation. The ExporterClass controller's structural comparison logic draws from these patterns.

- **Confluent Schema Registry** — Confluent's BACKWARD/FORWARD/FULL compatibility model provides a reference for future interface version evolution. Confluent checks `.proto` source text for wire-format compatibility (whether messages serialized with one schema can be deserialized with the other). The DriverInterface CRD operates on compiled `FileDescriptorProto` bytes rather than source text, but the compatibility semantics could inform future version evolution features.

- **Envoy gRPC-JSON transcoder** — Envoy accepts `FileDescriptorSet` as base64-encoded bytes in Kubernetes configuration for gRPC-JSON transcoding. This is a battle-tested pattern that validates the approach of storing serialized `FileDescriptorProto` inline in a CRD (`descriptor` field). No existing Kubernetes CRD stores proto descriptors inline for schema validation purposes — the DriverInterface CRD is novel in this regard, but the individual components (inline descriptor storage, structural comparison, compatibility semantics) all have proven prior art.

- **gRPC Server Reflection** — Already implemented in the JEP-0001 PoC. Drivers expose `FileDescriptorProto` at runtime via the gRPC reflection service, which is the source mechanism for comparison against the DriverInterface's canonical descriptor.

## Unresolved Questions

### Can wait until implementation

1. **Admission webhook:** Should the operator include a validating admission webhook that rejects malformed ExporterClasses (circular `extends`, missing `interfaceRef`) at apply time, or is controller-side validation with status conditions sufficient?

2. **Interface requirement weight/priority:** Should interface entries support a `priority` or `weight` field for lease scheduling? E.g., prefer exporters that satisfy more optional interfaces when multiple candidates match.

3. **ExporterClass discovery API:** Should `jmp admin get exporterclasses` query the cluster or work from local YAML files? Both have use cases — cluster for production, local for development.

## Future Possibilities

The following are **not** part of this JEP but are natural extensions enabled by it:

- **CEL-based selectors:** The current design uses standard Kubernetes label selectors. If more expressive power is needed (arbitrary boolean logic, string operations, access to structured device attributes), CEL expressions could be added as an alternative selector mechanism. The `cel-go` library is already available as an indirect dependency in the controller's module graph.

- **Polyglot typed device wrappers (JEP-0003):** The ExporterClass definition provides everything needed to generate typed device classes in any language — `EmbeddedLinuxBoardDevice` with `power: PowerClient`, `serial: SerialClient`, `storage: StorageClient` as non-nullable fields and `network: NetworkClient?` as nullable.

- **Driver registry integration (JEP-0004):** The registry can catalog which driver packages implement which DriverInterfaces, and which ExporterClasses they satisfy, enabling `jmp registry list exporter-classes` and `jmp registry describe exporter-class embedded-linux-board`. The registry could also serve as an alternative source for `proto.descriptor` resolution, supplementing the inline embedding approach.

- **Capacity planning dashboard:** With `status.satisfiedExporterCount` on every ExporterClass, a web dashboard could show real-time fleet capacity per device profile, utilization rates, and availability trends.

- **ExporterClass-aware scheduling:** The controller's lease scheduler could use ExporterClass satisfaction metadata for smarter scheduling — preferring exporters that satisfy the most optional interfaces, or load-balancing across ExporterClasses with the most available capacity.

- **Test matrix generation:** ExporterClass definitions could drive test matrix generation — automatically running a test suite against every ExporterClass that the test's required interfaces are a subset of.

## Implementation Phases

| Phase | Deliverable                                                                                                        | Depends On        |
| ----- | ------------------------------------------------------------------------------------------------------------------ | ----------------- |
| 1     | `DriverInterface` CRD definition + `make driver-interfaces` build target + bundled YAMLs in Helm/OLM               | JEP-0001          |
| 2     | `ExporterClass` CRD definition + controller validation on registration + lease enforcement                         | Phase 1           |
| 3     | `jmp admin` CLI tooling (`get/apply/generate driverinterface`, `get/apply exporterclass`, `jmp validate exporter`) | Phase 1           |
| 4     | Structural validation via `FileDescriptorProto` comparison (moderate strictness)                                   | Phase 2, JEP-0001 |
| 5     | ExporterClass inheritance (`extends`)                                                                              | Phase 2           |

Phases 1–2 are the minimum viable deliverable: named device contracts with controller-enforced lease matching, requiring no client-side changes. Phase 3 provides admin tooling. Phases 4–5 add the structural depth enabled by JEP-0001's proto introspection.

## Implementation History

- 2026-04-06: JEP drafted as "DeviceClass Mechanism"
- 2026-04-08: Renamed to "ExporterClass Mechanism" (`DeviceClass` → `ExporterClass`, `InterfaceClass` → `DriverInterface`). Replaced CEL selectors with standard Kubernetes label selectors. Changed CRD scope from cluster-scoped to namespace-scoped. Added schema distribution and CLI workflow section. Added `descriptor` for inline canonical `FileDescriptorProto` storage. Added feasibility assessment based on JEP-0001 PoC analysis. Added schema registry prior art (Buf BSR, Confluent, Envoy). Resolved all "must resolve before acceptance" design questions. Moved DriverInterface distribution to Jumpstarter installation (Helm/OLM bundling) instead of manual publishing. Moved admin CLI commands under `jmp admin`. Removed `config` section from ExporterClass (driver configuration belongs in ExporterConfig, not in the typing contract).

## References

- [JEP-0001: Protobuf Introspection and Interface Generation](./JEP-0001-protobuf-introspection-interface-generation.md)
- [Kubernetes DRA DeviceClass API (`resource.k8s.io/v1`)](https://kubernetes.io/docs/concepts/scheduling-eviction/dynamic-resource-allocation/)
- [Kubernetes DRA Setup Guide](https://kubernetes.io/docs/tasks/configure-pod-container/assign-resources/set-up-dra-cluster/)
- [Kubernetes DRA KEP-4381: Structured Parameters](https://github.com/kubernetes/enhancements/blob/master/keps/sig-node/4381-dra-structured-parameters/README.md)
- [Kubernetes StorageClass](https://kubernetes.io/docs/concepts/storage/storage-classes/)
- [Kubernetes Custom Resource Definitions](https://kubernetes.io/docs/tasks/extend-kubernetes/custom-resources/custom-resource-definitions/)
- [Buf Schema Registry](https://buf.build/docs/bsr/introduction)
- [Buf Breaking Change Detection](https://buf.build/docs/breaking/overview)
- [Confluent Schema Registry — Protobuf](https://docs.confluent.io/platform/current/schema-registry/fundamentals/serdes-develop/serdes-protobuf.html)
- [Envoy gRPC-JSON Transcoder](https://www.envoyproxy.io/docs/envoy/latest/configuration/http/http_filters/grpc_json_transcoder_filter)
- [gRPC Server Reflection](https://grpc.github.io/grpc/core/md_doc_server-reflection.html)
- [LAVA Device Types](https://docs.lavasoftware.org/lava/admin-lxc-deploy.html)
- [Jumpstarter Operator](https://docs.jumpstarter.dev/introduction/service.html)
- [Jumpstarter Lease System](https://docs.jumpstarter.dev/introduction/clients.html)

---

*This JEP is licensed under the
[Apache License, Version 2.0](https://www.apache.org/licenses/LICENSE-2.0),
consistent with the Jumpstarter project.*
