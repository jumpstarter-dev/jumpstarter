# JEP-0002: ExporterClass Mechanism

| Field              | Value                                                          |
|--------------------|----------------------------------------------------------------|
| **JEP**            | 0002                                                           |
| **Title**          | ExporterClass Mechanism                                        |
| **Author(s)**      | @kirkbrauer (Kirk Brauer)                                      |
| **Status**         | Draft                                                          |
| **Type**           | Standards Track                                                |
| **Created**        | 2026-04-06                                                     |
| **Updated**        | 2026-04-08                                                     |
| **Discussion**     | [Matrix](https://matrix.to/#/#jumpstarter:matrix.org)          |
| **Requires**       | JEP-0001 (Protobuf Introspection and Interface Generation)     |
| **Supersedes**     | —                                                              |
| **Superseded-By**  | —                                                              |

---

## Abstract

This JEP introduces an `ExporterClass` custom resource that defines a typed contract between device providers (exporters) and device consumers (test clients). An ExporterClass specifies required and optional driver interfaces by referencing `DriverInterface` CRDs, enabling the controller to structurally validate exporters at registration time using the `FileDescriptorProto` schemas from JEP-0001, and enabling client-side codegen (JEP-0003) to produce type-safe device wrappers with named accessors for each interface. An accompanying `DriverInterface` CRD links interface names to their canonical proto definitions and driver packages. Together, these resources bridge the gap between label-based infrastructure selection and typed API contracts.

## Motivation

Today, a client requesting a device lease specifies label selectors — `soc=sa8295p`, `vendor=acme` — and gets an exporter with matching metadata. But labels describe *infrastructure* (what hardware is connected), not *API contracts* (what driver interfaces are available). A client that receives a lease has no guarantee about which driver interfaces the exporter provides. It must call `GetReport`, walk the driver tree, check for the presence of each interface by name, and handle missing interfaces at runtime. This makes it impossible to write type-safe client code in any language.

The consequences are concrete:

- **No compile-time safety.** A test that needs `power.on()`, `serial.connect()`, and `flash.write()` cannot know at compile time whether the leased device provides all three. If the serial driver is missing, the test discovers this at runtime — potentially minutes into a CI pipeline — with an opaque `KeyError` or `AttributeError`.

- **No contract for exporters.** An operator deploying a new exporter for automotive headunit testing has no way to verify that the exporter's driver configuration satisfies the requirements of the test suites that will lease it. Misconfiguration is discovered when tests fail, not at deployment time.

- **No typed codegen.** Without a formal declaration of which interfaces a device provides, code generation tools (JEP-0003) cannot produce typed device wrappers with non-nullable accessors. Every interface accessor must be `Optional`, defeating the purpose of typed clients.

- **No fleet-level visibility.** There is no aggregated view of how many exporters satisfy a particular device profile. An operator cannot answer "how many android-headunit devices are available right now?" without scripting custom `GetReport` queries across the fleet.

ExporterClass bridges the gap between infrastructure selection (labels) and API contracts (interfaces), following the established Kubernetes pattern used by `StorageClass`, `IngressClass`, and `RuntimeClass`.

### User Stories

- **As a** CI pipeline author writing tests for an Android headunit, **I want to** request a lease by ExporterClass name rather than memorizing which labels correspond to which capabilities, **so that** my pipeline gets a device guaranteed to have power, ADB, flash, and serial interfaces — or fails immediately if none is available.

- **As a** platform operator deploying a new exporter rack, **I want to** validate my exporter configuration against the `android-headunit` ExporterClass before connecting it to the controller, **so that** I catch missing drivers or interface mismatches at deployment time rather than when a test fails at 2 AM.

- **As a** test framework developer generating typed Kotlin clients for tradefed, **I want** the ExporterClass definition to tell me exactly which interfaces are required vs. optional, **so that** I can generate non-nullable accessors for required interfaces and `Optional` accessors for optional ones.

- **As a** fleet manager responsible for 50+ exporters across three labs, **I want to** see at a glance how many exporters satisfy each ExporterClass and which ones are missing interfaces, **so that** I can plan capacity and prioritize hardware procurement.

- **As a** driver developer adding CAN bus support to an existing exporter, **I want to** check whether my updated exporter configuration now satisfies the `android-headunit` ExporterClass (which lists CAN as optional), **so that** I can verify my work without running the full test suite.

## Proposal

### Overview

This proposal introduces two new Kubernetes CRDs, one protocol extension, and CLI tooling for schema distribution:

1. **`DriverInterface`** — a namespace-scoped CRD that names an interface, references its canonical proto definition (from JEP-0001), and specifies how to identify drivers implementing it in a `DriverInstanceReport`.
2. **`ExporterClass`** — a namespace-scoped CRD that declares a named device profile as a set of required and optional `DriverInterface` references, plus label selectors.
3. **`RequestLeaseRequest.exporter_class_name`** — a new optional field in the lease protocol that binds a lease to an ExporterClass contract.
4. **CLI tooling** — commands for publishing `DriverInterface` CRDs from driver packages, applying `ExporterClass` definitions, and validating exporter configurations locally.

The controller validates exporters against ExporterClasses at registration time using the `FileDescriptorProto` descriptors embedded in `DriverInstanceReport` (JEP-0001), and filters lease candidates against ExporterClass requirements at lease time. Client-side tooling and codegen consume ExporterClass definitions to produce typed device wrappers.

### DriverInterface CRD

A `DriverInterface` is a lightweight registry entry that gives a stable name to a driver interface and links it to its canonical proto definition:

```yaml
apiVersion: jumpstarter.dev/v1alpha1
kind: DriverInterface
metadata:
  name: power-v1
  namespace: lab-detroit
  labels:
    jumpstarter.dev/interface-group: jumpstarter.interfaces.power
    jumpstarter.dev/interface-version: v1
spec:
  # Reference to the canonical proto definition (from JEP-0001)
  protoRef:
    module: pkg.jumpstarter.dev/jumpstarter-interfaces/power
    version: v1
    # Inline canonical FileDescriptorProto bytes (base64-encoded)
    # Generated by: jmp interface publish --dry-run <interface-class>
    inlineDescriptor: CpoBMQpwanVtcHN0YXJ0ZXIvaW50ZXJmYWNlcy9wb3dlci92MS...

  # Python package that provides this interface
  driverPackage:
    name: jumpstarter-driver-power
    version: ">=1.0.0,<2.0.0"
    index: https://pkg.jumpstarter.dev/

  # Interface matching — how to identify this interface in GetReport
  match:
    label: jumpstarter.dev/interface
    value: Power

  # Version compatibility chain
  compatibleWith: []  # power-v1 is the first version

status:
  implementationCount: 15
  conditions:
    - type: Ready
      status: "True"
```

The `protoRef` field references the `.proto` file or `FileDescriptorProto` artifact produced by JEP-0001's `jmp interface generate`. The `inlineDescriptor` field contains the canonical `FileDescriptorProto` as base64-encoded bytes, used by the controller for structural validation. This follows the pattern established by Envoy's gRPC-JSON transcoder, which embeds `FileDescriptorSet` bytes inline in Kubernetes configuration. The `match` criteria specify how the controller identifies this interface in an exporter's `DriverInstanceReport` — by checking for a `jumpstarter.dev/interface` label on drivers in the report tree. The `driverPackage` field tells clients which Python package to install to get the interface class and client.

The `compatibleWith` field enables interface evolution: when `power-v2` is published, it can declare `compatibleWith: [power-v1]`, indicating that exporters implementing `power-v2` also satisfy ExporterClass requirements that reference `power-v1`.

### ExporterClass CRD

An `ExporterClass` declares a device profile as a set of selectors and interface requirements:

```yaml
apiVersion: jumpstarter.dev/v1alpha1
kind: ExporterClass
metadata:
  name: android-headunit
  namespace: lab-detroit
spec:
  # Standard Kubernetes label selectors for exporter matching.
  # Each selector must be satisfied by an exporter to match this ExporterClass.
  selector:
    matchLabels:
      platform: android
    matchExpressions:
      - key: type
        operator: In
        values: [headunit, infotainment]

  # Interface requirements — validated against DriverInstanceReport using DriverInterface matching.
  interfaces:
    - name: power               # accessor name in generated client
      interfaceRef: power-v1    # references a DriverInterface
      required: true
    - name: adb
      interfaceRef: adb-v1
      required: true
    - name: flash
      interfaceRef: storage-mux-flasher-v1
      required: true
    - name: serial
      interfaceRef: serial-v1
      required: true
    - name: can
      interfaceRef: can-v1alpha1
      required: false

  # Driver-specific configuration — opaque parameters passed to drivers when a lease is bound.
  config:
    - opaque:
        driver: jumpstarter.dev/power
        parameters:
          apiVersion: jumpstarter.dev/v1alpha1
          kind: PowerConfig
          cycleDelaySeconds: 5

status:
  satisfiedExporterCount: 12
  resolvedInterfaces: [power-v1, adb-v1, storage-mux-flasher-v1, serial-v1, can-v1alpha1]
  conditions:
    - type: Ready
      status: "True"
      reason: "ExportersSatisfied"
      message: "12 exporters satisfy all required interfaces"
```

The `spec` has three sections:

**`selector`** — standard Kubernetes label selectors (`matchLabels` and `matchExpressions`) that each candidate exporter must satisfy. This uses the same label selector mechanism already used by the lease controller, making it familiar to operators and requiring no additional dependencies.

**`config`** — an optional list of driver-specific opaque parameters. These parameters are passed to drivers when a lease is bound but are not considered during exporter matching. This allows an ExporterClass to configure driver behavior (e.g., power cycle delay, serial baud rate defaults) without conflating configuration with selection.

**`interfaces`** — a Jumpstarter-specific extension. Each entry has three fields: `name` (the accessor name used in generated client code — e.g., `device.power`, `device.serial`), `interfaceRef` (a reference to a `DriverInterface` by name), and `required` (whether the interface must be present for an exporter to satisfy this ExporterClass). This section is what makes ExporterClass a *typed API contract* rather than just a selector — it declares which driver interfaces the device must provide, enabling codegen and structural validation.

### ExporterClass Inheritance

ExporterClasses can extend other ExporterClasses to create specialization hierarchies:

```yaml
apiVersion: jumpstarter.dev/v1alpha1
kind: ExporterClass
metadata:
  name: android-device
  namespace: lab-detroit
spec:
  selector:
    matchLabels:
      platform: android
  interfaces:
    - name: power
      interfaceRef: power-v1
      required: true
    - name: adb
      interfaceRef: adb-v1
      required: true
---
apiVersion: jumpstarter.dev/v1alpha1
kind: ExporterClass
metadata:
  name: android-headunit
  namespace: lab-detroit
spec:
  extends: android-device  # inherits selector + power + adb
  selector:
    matchExpressions:
      - key: type
        operator: In
        values: [headunit, infotainment]
  interfaces:
    - name: flash
      interfaceRef: storage-mux-flasher-v1
      required: true
    - name: serial
      interfaceRef: serial-v1
      required: true
    - name: can
      interfaceRef: can-v1alpha1
      required: false
```

Tests written against `android-device` run on any Android target (phones, tablets, headunits). Tests written against `android-headunit` require the automotive-specific interfaces (flash, serial, CAN) in addition to the base power + ADB. An exporter that satisfies `android-headunit` automatically satisfies `android-device` — the inheritance is structural, not declared on the exporter.

The `extends` chain is resolved at ExporterClass reconciliation time, not at lease time. The flattened interface list is cached in `status.resolvedInterfaces`. Circular `extends` chains are detected during reconciliation and result in a `Degraded` condition.

### Enhanced Lease Protocol

The `RequestLeaseRequest` message gains an optional `exporter_class_name` field:

```protobuf
message RequestLeaseRequest {
  google.protobuf.Duration duration = 1;
  LabelSelector selector = 2;
  optional string exporter_class_name = 3;  // NEW
}
```

The CLI gains a `--exporter-class` flag:

```bash
jmp create lease --exporter-class android-headunit --selector backend=physical --duration 1h
```

When `exporter_class_name` is set, the controller resolves the ExporterClass, merges its `selector` with the request's `selector`, and additionally validates that candidate exporters satisfy all `required: true` interfaces. The `selector` field remains available for further filtering — a CI job might request `--exporter-class android-headunit --selector lab=detroit` to get a headunit specifically from the Detroit lab.

### Schema Distribution and CLI Workflow

A key operational question is how `DriverInterface` and `ExporterClass` CRDs are authored, distributed, and applied to clusters. This JEP defines a workflow that integrates with the existing driver packaging and discovery mechanisms.

#### DriverInterface distribution

`DriverInterface` YAML definitions are generated from and shipped alongside driver packages. Driver packages already contain proto definitions (e.g., `jumpstarter-driver-power/proto/power/v1/power.proto`) and register via Python entry points (`jumpstarter.drivers` group). The `DriverInterface` CRD extends this by adding a Kubernetes-native representation of the interface contract.

**For bundled/community drivers** (e.g., Android Emulator, Power, Serial):
- `DriverInterface` YAML is checked into the driver source package under `k8s/` (e.g., `jumpstarter-driver-power/k8s/driverinterface.yaml`)
- Published alongside the Python package
- Operators apply with `kubectl apply -f` or via a Helm chart / kustomize overlay

**For custom/private drivers:**
- `jmp interface publish` generates the `DriverInterface` CRD YAML from the installed driver's metadata and `FileDescriptorProto`, then applies it to the cluster

#### CLI commands

```bash
# Generate and apply a DriverInterface CRD from an installed driver
jmp interface publish jumpstarter_driver_power.driver.PowerInterface

# Generate DriverInterface YAML without applying (for GitOps workflows)
jmp interface publish --dry-run jumpstarter_driver_power.driver.PowerInterface > power-v1.yaml

# List DriverInterface CRDs in the current namespace
jmp interface list

# Apply an ExporterClass to the cluster
jmp exporter-class apply android-headunit.yaml

# List ExporterClasses in the current namespace
jmp exporter-class list

# Validate an exporter config against an ExporterClass locally (no cluster needed)
jmp validate --exporter /etc/jumpstarter/exporters/headunit.yaml \
             --exporter-class android-headunit.yaml
```

#### Discovery flow

1. A driver developer creates a driver package with `@driverinterface("Power", version="v1")` and `@export` methods.
2. `jmp interface generate` produces the `.proto` file (JEP-0001).
3. `jmp interface publish` generates the `DriverInterface` CRD YAML with the inline `FileDescriptorProto` descriptor and applies it to the cluster (or outputs YAML for GitOps).
4. A lab admin authors an `ExporterClass` YAML referencing `DriverInterface` CRDs by name.
5. `jmp exporter-class apply` or `kubectl apply` applies the `ExporterClass`.
6. The controller validates exporters against ExporterClasses at registration time.

### Controller Validation Flow

#### On exporter registration (`Register` RPC)

1. Receive exporter's labels and `DriverInstanceReport` tree (which now includes `file_descriptor_proto` from JEP-0001).
2. For each `ExporterClass` in the namespace:
   a. Evaluate the label selector against the exporter's labels using standard Kubernetes `labels.Selector` matching. All selector criteria must be satisfied.
   b. For each `required: true` interface entry, resolve the referenced `DriverInterface` and verify a driver in the report tree has a matching `jumpstarter.dev/interface` label corresponding to the DriverInterface's `match` criteria.
   c. When a matching driver is found, optionally compare its `file_descriptor_proto` against the `DriverInterface`'s canonical proto (from `inlineDescriptor`) for structural validation — verifying that method names, parameter types, return types, and streaming semantics are compatible.
3. Tag the exporter with satisfied ExporterClasses (stored in `ExporterStatus.SatisfiedExporterClasses`).
4. Update each satisfied ExporterClass's `status.satisfiedExporterCount`.

#### On lease request with `exporter_class_name`

1. Resolve the ExporterClass (including any `extends` chain, using the cached `status.resolvedInterfaces`).
2. Collect the merged label selector from the ExporterClass hierarchy.
3. Filter exporters to those tagged as satisfying the ExporterClass.
4. Apply additional label constraints from the request's `selector` field.
5. Bind lease to the best available exporter.

If no exporter satisfies the ExporterClass, the lease request fails immediately with a descriptive error indicating which interfaces or labels are unsatisfied — rather than timing out waiting for an exporter that will never match.

### Local Validation Tool

```bash
# Validate an exporter config against an ExporterClass before deployment
jmp validate --exporter /etc/jumpstarter/exporters/headunit.yaml \
             --exporter-class android-headunit.yaml
```

This runs the interface matching locally without requiring a running controller. It loads the exporter configuration, instantiates the drivers (or introspects them without full initialization), and checks each interface requirement against the driver tree. Output indicates which requirements are satisfied, which are missing, and which are optional:

```text
ExporterClass: android-headunit
  ✓ power      (power-v1)                   → DutlinkPower
  ✓ adb        (adb-v1)                     → AdbDevice
  ✓ flash      (storage-mux-flasher-v1)     → DutlinkStorageMux
  ✓ serial     (serial-v1)                  → PySerial
  ○ can        (can-v1alpha1)  [optional]   → not found

Result: SATISFIED (4/4 required, 0/1 optional)
```

This provides fast feedback during driver development and exporter deployment — an operator can verify compliance before connecting the exporter to the controller.

### Structural Validation with `FileDescriptorProto`

JEP-0001 embeds a `FileDescriptorProto` in each driver's `DriverInstanceReport`. ExporterClass validation can go beyond label matching and perform structural comparison using two complementary strategies:

**Label-based matching** (backward compatible): The `DriverInterface` CRD's `match` criteria specify a label (`jumpstarter.dev/interface`) and value (e.g., `Power`). The controller checks for this label on drivers in the report tree. This works for all exporters, including pre-JEP-0001 deployments that don't embed `FileDescriptorProto`.

**Descriptor-based matching** (JEP-0001 exporters): When a matching driver is found and both the driver and the `DriverInterface` have `FileDescriptorProto` descriptors available, the controller performs structural comparison:

1. The `DriverInterface` CRD's `inlineDescriptor` contains the canonical `FileDescriptorProto` — the reference schema for that interface at that version.
2. The exporter's driver report contains the driver's `FileDescriptorProto` — the schema that the driver actually implements.
3. The controller (or `jmp validate`) compares the two descriptors to verify structural compatibility: are all required methods present? Do their parameter types, return types, and streaming semantics match?

For the Experimental phase, structural validation uses **moderate strictness**: method names must exist, input/output message field types must match, and streaming semantics must match. Field number comparison is deferred to the Stable phase, since JEP-0001's builder generates deterministic field numbers that may differ from hand-written protos.

This moves validation from convention-based (label says "Power") to structure-based (descriptor proves the driver has `on()`, `off()`, and `read()` with correct signatures). A driver that claims to implement `power-v1` but is missing the `read()` streaming method, or has changed `off()` to require a parameter, is caught at registration time — not when a test fails.

The structural validation is additive — it enhances label-based matching without replacing it. Exporters that don't yet embed `FileDescriptorProto` (pre-JEP-0001 deployments) still work through label matching alone.

### API / Protocol Changes

#### New CRDs

Two new namespace-scoped CRDs are added to the Jumpstarter operator:

- `driverinterfaces.jumpstarter.dev/v1alpha1` — `DriverInterface`
- `exporterclasses.jumpstarter.dev/v1alpha1` — `ExporterClass`

Both CRDs are namespace-scoped to support multi-tenant lab environments where different teams manage their own device profiles and interface definitions within their namespace. This diverges from the Kubernetes `*Class` convention (where `StorageClass`, `IngressClass`, etc. are cluster-scoped) but better fits Jumpstarter's deployment model, where multiple labs or teams share a single cluster with independent device profiles.

Both are additive — they don't modify existing CRDs (`Exporter`, `Client`, `Lease`).

#### Modified CRD

The `ExporterStatus` struct gains a new field:

```go
type ExporterStatus struct {
    // ... existing fields ...
    SatisfiedExporterClasses []string `json:"satisfiedExporterClasses,omitempty"`
}
```

This avoids runtime re-evaluation of ExporterClass satisfaction at lease time.

#### Modified Protocol

The `RequestLeaseRequest` message gains one optional field:

```protobuf
message RequestLeaseRequest {
  google.protobuf.Duration duration = 1;
  LabelSelector selector = 2;
  optional string exporter_class_name = 3;  // NEW — additive, backward compatible
}
```

Old clients that don't set `exporter_class_name` continue to work with label-only leases.

#### Modified CLI

The `jmp create lease` command gains a `--exporter-class` flag. The `jmp validate`, `jmp interface publish`, `jmp interface list`, `jmp exporter-class apply`, and `jmp exporter-class list` commands are new.

### Hardware Considerations

This JEP is a control-plane change. No hardware is required or affected. ExporterClass and DriverInterface are Kubernetes CRDs processed by the controller. The validation logic operates on `DriverInstanceReport` metadata and `FileDescriptorProto` descriptors — it does not interact with physical devices or timing-sensitive operations.

The `jmp validate` tool loads exporter configurations and introspects driver classes but does not initialize hardware. It runs on the operator's workstation, not on the exporter host.

## Design Details

### Architecture

```text
┌────────────────────────┐     ┌────────────────────────┐
│   DriverInterface CRD  │     │   DriverInterface CRD  │
│    power-v1            │     │    serial-v1           │
│    inlineDescriptor:.. │     │    inlineDescriptor:.. │
└───────────┬────────────┘     └───────────┬────────────┘
            │                              │
            └──────────┬───────────────────┘
                       │  referenced by
                       ▼
            ┌──────────────────────┐
            │  ExporterClass CRD   │
            │   android-headunit   │
            │   interfaces:        │
            │     power: power-v1  │
            │     serial: serial-v1│
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
            │   │  └─ ...       │ │
            │   └────────────────┘ │
            └──────────────────────┘
```

### Data Flow

1. **Driver development:** A driver developer creates a package with `@driverinterface` and `@export` methods. `jmp interface generate` produces the `.proto` file. `jmp interface publish` generates and applies the `DriverInterface` CRD.
2. **ExporterClass creation:** A lab admin applies `ExporterClass` YAML manifests to the cluster, referencing `DriverInterface` CRDs by name.
3. **Exporter registration:** An exporter calls `Register` with its labels and `DriverInstanceReport` tree. The controller evaluates the exporter against all ExporterClasses in the namespace and tags it with the ones it satisfies.
4. **Lease request:** A client requests a lease with `exporter_class_name`. The controller filters to exporters tagged as satisfying that ExporterClass, applies additional selectors, and binds the lease.
5. **ExporterClass status:** The controller maintains `status.satisfiedExporterCount` on each ExporterClass, updated on exporter registration, deregistration, and configuration changes.

### Resolved Design Decisions

#### CRD Scope: Namespace-scoped

ExporterClass and DriverInterface are namespace-scoped to support multi-tenant lab environments. Different teams can define their own device profiles and interface definitions within their namespace. Lease matching already operates within namespaces in the existing controller. While this diverges from the K8s `*Class` convention (StorageClass, IngressClass are cluster-scoped), it better fits Jumpstarter's multi-lab deployment model where teams need independent control over their device profiles.

#### Selectors: Standard Kubernetes Label Selectors

ExporterClass uses standard `matchLabels`/`matchExpressions` selectors instead of CEL expressions. This eliminates the need to add a direct CEL dependency (though `cel-go` is available as an indirect dependency via `k8s.io/apiserver`), matches the existing pattern in the lease controller (which already uses `labels.Selector`), and is simpler for operators familiar with standard Kubernetes label selectors. CEL-based selectors can be added as a future enhancement if more expressive power is needed.

#### Structural Validation Strictness: Moderate for Experimental

During the Experimental phase, structural validation compares: method names (all canonical methods must exist in the driver descriptor), input/output message field types (field names and proto types must match), and streaming semantics (server_streaming, client_streaming flags must match). Field number comparison is deferred to the Stable phase, since JEP-0001's `build_file_descriptor()` generates deterministic field numbers that may differ from hand-written proto files.

#### Canonical Descriptor Storage: Inline Embedding

The `DriverInterface` CRD stores the canonical `FileDescriptorProto` as base64-encoded bytes in `spec.protoRef.inlineDescriptor`. The `module` and `version` fields in `protoRef` remain as human-readable metadata for documentation and discoverability, but the controller uses `inlineDescriptor` for validation. This follows the pattern established by Envoy's gRPC-JSON transcoder, which embeds `FileDescriptorSet` bytes inline in Kubernetes configuration. A future JEP-0004 (Driver Registry) can add registry-based resolution as an alternative source.

### Error Handling and Failure Modes

- **Missing DriverInterface:** If an ExporterClass references an `interfaceRef` that doesn't exist as a DriverInterface CRD, the ExporterClass enters a `Degraded` condition with a descriptive message. Exporters are not validated against the missing interface.
- **Unsatisfied ExporterClass lease:** If a lease request specifies an `exporter_class_name` and no available exporter satisfies it, the controller returns an immediate error with details: which interfaces are missing across the fleet, and how many exporters partially satisfy the ExporterClass.
- **Circular inheritance:** If ExporterClass `extends` creates a cycle (A extends B extends A), the controller detects this at reconciliation time and sets a `Degraded` condition with a descriptive error.
- **Stale exporter tags:** If an ExporterClass is modified (e.g., a new required interface is added), the controller re-evaluates all tagged exporters and removes tags from those that no longer satisfy the updated requirements.

### Concurrency and Thread-Safety

ExporterClass and DriverInterface are standard Kubernetes CRDs, managed by the controller's reconciliation loop. The controller uses Kubernetes informers with watch/list semantics — standard patterns for handling concurrent updates. Exporter tagging uses status subresources with optimistic concurrency (resourceVersion-based conflict detection). The ExporterClass reconciler watches Exporter resources (via `Watches()` in `SetupWithManager`) to update `satisfiedExporterCount` when exporters register or deregister.

### Security Implications

ExporterClass and DriverInterface CRDs are namespace-scoped resources, subject to standard Kubernetes RBAC. Only users with appropriate roles can create, modify, or delete them within their namespace. The `jmp validate` tool runs locally and does not require cluster access — it reads ExporterClass YAML files from the filesystem.

The structural validation against `FileDescriptorProto` uses the same authenticated `GetReport` data that the controller already receives during exporter registration. No additional authentication or transport security is required.

## Feasibility Assessment

### JEP-0001 PoC Readiness

The JEP-0001 PoC (commit `b40abc06`) provides a solid foundation for this JEP. The following capabilities are already implemented and ready:

| Capability | Status |
|---|---|
| `FileDescriptorProto` in `DriverInstanceReport` (end-to-end flow) | Ready |
| `@driverinterface(name, version)` decorator | Ready |
| `build_file_descriptor()` producing canonical descriptors | Ready |
| `DriverInterfaceMeta._registry` interface registry | Ready |
| `Device.FileDescriptorProto []byte` in Go controller types | Ready |
| gRPC Server Reflection | Ready |
| Driver entry point registration (`jumpstarter.drivers` group) | Ready |
| `jmp driver list` discovery via `importlib.metadata` | Ready |
| `jmp interface generate` producing .proto from Python interfaces | Ready |
| Proto files shipping with driver packages | Ready |

### Gaps Requiring Implementation

1. **`jumpstarter.dev/interface` label**: Drivers currently emit only `jumpstarter.dev/client` labels. A one-line addition to `Driver.report()` using `_get_interface_class().__interface_name__` is needed (Phase 0 prerequisite).

2. **FileDescriptorProto parsing in Go controller**: The `Device.FileDescriptorProto` bytes are stored opaquely. Go-side deserialization using `google.golang.org/protobuf/types/descriptorpb` (already in dependencies) is needed for structural validation.

3. **ExporterClass/DriverInterface CRDs**: Two new namespace-scoped CRDs following existing kubebuilder patterns.

4. **Lease protocol extension**: `optional string exporter_class_name = 3` in `RequestLeaseRequest`.

5. **CLI tooling**: `jmp interface publish`, `jmp exporter-class apply/list`, `jmp validate` commands.

## Test Plan

### Unit Tests

- **DriverInterface matching:** Verify that the controller correctly identifies drivers in a `DriverInstanceReport` tree that match a DriverInterface's `match` criteria (label + value).
- **ExporterClass evaluation:** Verify that an exporter is correctly tagged as satisfying an ExporterClass when all required interfaces are present in the report tree, and not tagged when any required interface is missing.
- **Optional interface handling:** Verify that missing optional interfaces do not prevent an exporter from satisfying an ExporterClass.
- **ExporterClass inheritance:** Verify that an `extends` chain correctly merges interface requirements from parent and child ExporterClasses.
- **Circular inheritance detection:** Verify that a circular `extends` chain is detected and results in a `Degraded` condition.
- **Structural validation:** Verify that `FileDescriptorProto` comparison catches method mismatches (missing methods, type differences, streaming semantics changes) between a driver's descriptor and the DriverInterface's canonical descriptor.
- **DriverInterface compatibility:** Verify that `compatibleWith` allows a driver implementing `power-v2` to satisfy an ExporterClass requiring `power-v1`.
- **Label selector evaluation:** Verify that `matchLabels` and `matchExpressions` correctly filter exporters by their labels, and that all selector criteria must pass for a match.
- **Selector merging:** Verify that the controller correctly applies ExporterClass label selectors alongside the lease request's `selector`.

### Integration Tests

- **End-to-end lease with ExporterClass:** Apply DriverInterface and ExporterClass CRDs, register an exporter, request a lease with `--exporter-class`, and verify the lease binds to the correct exporter.
- **Unsatisfied ExporterClass:** Register an exporter missing a required interface, request a lease with `--exporter-class`, and verify an immediate descriptive error.
- **`jmp validate` end-to-end:** Run `jmp validate` against an exporter configuration and an ExporterClass YAML, verify correct pass/fail output for various configurations.
- **`jmp interface publish` end-to-end:** Run `jmp interface publish` for a driver package and verify the DriverInterface CRD is created with correct `inlineDescriptor`.
- **ExporterClass status:** Register multiple exporters, verify `status.satisfiedExporterCount` is accurate, deregister one, verify the count decrements.
- **CRD update re-evaluation:** Modify an ExporterClass to add a new required interface, verify exporters are re-evaluated and tags are updated.

### Hardware-in-the-Loop Tests

No HiL tests are required for this JEP. ExporterClass is a control-plane feature operating on CRDs and `DriverInstanceReport` metadata. The `jmp validate` tool introspects driver classes without initializing hardware.

### Manual Verification

- Apply the `android-headunit` ExporterClass and associated DriverInterfaces to a test cluster. Register exporters with varying driver configurations and verify the ExporterClass `status` reflects the correct count.
- Run `jmp create lease --exporter-class android-headunit` and verify the correct exporter is selected.
- Run `jmp validate` against a real exporter configuration and verify the output accurately reflects the exporter's interface compliance.
- Run `jmp interface publish` for at least three driver packages and verify correct DriverInterface CRD generation.

## Graduation Criteria

### Experimental

- DriverInterface and ExporterClass CRDs are installable on a Jumpstarter operator deployment.
- Controller validates exporters against ExporterClasses at registration time using label-based matching.
- `jmp create lease --exporter-class` works for at least one ExporterClass in a test cluster.
- `jmp validate` produces correct output for at least three exporter configurations.
- `jmp interface publish` generates correct DriverInterface CRDs for at least three driver packages.
- At least one ExporterClass (`android-headunit` or `embedded-linux`) is defined and used in the project's CI.

### Stable

- Structural validation using `FileDescriptorProto` comparison is implemented and enabled by default (moderate strictness: method names, type structure, streaming semantics).
- ExporterClass inheritance (`extends`) is implemented and tested.
- `status.satisfiedExporterCount` is maintained accurately across exporter lifecycle events.
- At least one downstream JEP (Codegen or Registry) consumes ExporterClass definitions.
- DriverInterface `compatibleWith` versioning is implemented.
- No breaking changes to the CRD schema for at least one release cycle.
- Field number comparison added to structural validation (strict mode).

## Backward Compatibility

This JEP is **fully backward compatible.** All changes are additive:

- `exporter_class_name` is an optional field in `RequestLeaseRequest`. Existing clients that don't set it continue to use label-only leases with no behavioral change.

- The `ExporterClass` and `DriverInterface` CRDs are new resources. They don't modify existing CRDs (`Exporter`, `Client`, `Lease`). Clusters without these CRDs installed behave exactly as before.

- Exporter registration is unchanged. The controller's additional ExporterClass evaluation is a read-only check that tags exporters with satisfied ExporterClasses. Exporters that predate ExporterClass are simply not tagged — they remain available for label-only leases.

- Structural validation via `FileDescriptorProto` is additive to label matching. Exporters that don't embed `FileDescriptorProto` (pre-JEP-0001) are validated by label matching alone. The structural check is a superset, not a replacement.

- The `jmp validate`, `jmp interface publish`, `jmp interface list`, `jmp exporter-class apply`, and `jmp exporter-class list` commands are new. The `jmp create lease` command gains an optional `--exporter-class` flag without changing existing flags or behavior.

## Rejected Alternatives

### Embedding interface requirements in labels

An early approach considered encoding interface requirements as exporter labels (e.g., `jumpstarter.dev/has-power=true`, `jumpstarter.dev/has-serial=true`) and matching them with standard label selectors. This was rejected because labels are unstructured strings with no validation — they can't express versioning, optional vs. required semantics, or structural compatibility. They also pollute the label space and require manual synchronization between exporter configuration and label values.

### Using annotations instead of CRDs

An alternative considered storing ExporterClass definitions as annotations on a shared ConfigMap. This was rejected because annotations have a 256 KB size limit, lack schema validation, don't support status subresources, and don't integrate with Kubernetes RBAC or the controller's informer/watch infrastructure.

### Defining ExporterClass as a gRPC-only API (no CRD)

An alternative considered defining ExporterClass as a gRPC service on the controller (like the Registry in JEP-0004) rather than a Kubernetes CRD. This was rejected because CRDs provide declarative management via `kubectl apply`, RBAC integration, status subresources, and watch semantics for free — all of which a gRPC API would need to reimplement. ExporterClasses are cluster configuration, not runtime data; CRDs are the natural Kubernetes primitive for this.

### Requiring all interfaces to be required (no optional)

A simpler model considered making all interfaces in an ExporterClass required, with no optional flag. This was rejected because real device profiles have varying capabilities — CAN bus is available on some headunits but not all, and tests should be able to target the common denominator (`android-device`) or the full profile (`android-headunit`) as appropriate.

### Cluster-scoped CRDs

The Kubernetes `*Class` convention (StorageClass, IngressClass, RuntimeClass) uses cluster-scoped resources. This was considered but rejected for Jumpstarter because namespace-scoping better supports multi-tenant lab environments where different teams need independent control over their device profiles and interface definitions. Lease matching already operates within namespaces, and namespace-scoping provides natural RBAC isolation without additional configuration.

## Prior Art

- **Kubernetes DRA DeviceClass** (`resource.k8s.io/v1`) — a design influence. Jumpstarter's ExporterClass aligns with the K8s DRA `DeviceClass` in several ways: named CRD, selectors, opaque driver configuration (`spec.config`), and the overall pattern of a named class that defines selection criteria for devices. Where Jumpstarter diverges is the `interfaces` section — K8s DRA selects devices by attributes and capacity (e.g., GPU memory, driver name), while Jumpstarter selects exporters by the driver interfaces they provide (e.g., power, serial, ADB). The ExporterClass also uses standard label selectors instead of K8s DRA's CEL-based selectors, and is namespace-scoped instead of cluster-scoped. K8s DRA uses `ResourceClaim`/`ResourceClaimTemplate` for allocation; Jumpstarter uses its own Lease mechanism.

- **Kubernetes StorageClass / IngressClass / RuntimeClass** — Kubernetes uses the `*Class` pattern extensively to abstract infrastructure profiles into named contracts. `StorageClass` maps a name to a storage provisioner with parameters; `IngressClass` maps a name to an ingress controller. Jumpstarter's ExporterClass follows the same naming convention.

- **LAVA device types** (Linaro Automated Validation Architecture) — LAVA uses device type definitions (Jinja2 templates) to describe hardware capabilities and select compatible test jobs. Jumpstarter's ExporterClass is more strongly typed (label selectors + proto-based structural validation vs. YAML templates) but serves the same matching purpose in HiL testing.

- **OpenAPI / Swagger schemas** — OpenAPI defines API contracts that are validated at request time. ExporterClass performs an analogous validation at the infrastructure level — verifying that a device provides the API contract that test code expects.

- **Buf Schema Registry (BSR)** — Buf handles proto module versioning and breaking change detection via structural comparison of `FileDescriptorProto` at the descriptor level. The `buf breaking` command's WIRE category rules (checking for removed RPCs, changed field types/numbers, streaming semantics changes) are directly applicable to DriverInterface structural validation. The ExporterClass controller's structural comparison logic draws from these patterns.

- **Confluent Schema Registry** — Confluent's BACKWARD/FORWARD/FULL compatibility model maps directly to the `compatibleWith` chain concept in the DriverInterface CRD. Confluent checks `.proto` source text for wire-format compatibility (whether messages serialized with one schema can be deserialized with the other). The DriverInterface CRD operates on compiled `FileDescriptorProto` bytes rather than source text, but the compatibility semantics are analogous.

- **Envoy gRPC-JSON transcoder** — Envoy accepts `FileDescriptorSet` as base64-encoded bytes in Kubernetes configuration for gRPC-JSON transcoding. This is a battle-tested pattern that validates the approach of storing serialized `FileDescriptorProto` inline in a CRD (`inlineDescriptor` field). No existing Kubernetes CRD stores proto descriptors inline for schema validation purposes — the DriverInterface CRD is novel in this regard, but the individual components (inline descriptor storage, structural comparison, compatibility semantics) all have proven prior art.

- **gRPC Server Reflection** — Already implemented in the JEP-0001 PoC. Drivers expose `FileDescriptorProto` at runtime via the gRPC reflection service, which is the source mechanism for comparison against the DriverInterface's canonical descriptor.

## Unresolved Questions

### Can wait until implementation

1. **Admission webhook:** Should the operator include a validating admission webhook that rejects malformed ExporterClasses (circular `extends`, missing `interfaceRef`) at apply time, or is controller-side validation with status conditions sufficient?

2. **Interface requirement weight/priority:** Should interface entries support a `priority` or `weight` field for lease scheduling? E.g., prefer exporters that satisfy more optional interfaces when multiple candidates match.

3. **ExporterClass discovery API:** Should `jmp exporter-class list` query the cluster or work from local YAML files? Both have use cases — cluster for production, local for development.

## Future Possibilities

The following are **not** part of this JEP but are natural extensions enabled by it:

- **CEL-based selectors:** The current design uses standard Kubernetes label selectors. If more expressive power is needed (arbitrary boolean logic, string operations, access to structured device attributes), CEL expressions could be added as an alternative selector mechanism. The `cel-go` library is already available as an indirect dependency in the controller's module graph.

- **Polyglot typed device wrappers (JEP-0003):** The ExporterClass definition provides everything needed to generate typed device classes in any language — `AndroidHeadunitDevice` with `power: PowerClient`, `adb: AdbClient`, `serial: SerialClient` as non-nullable fields and `can: CanClient?` as nullable.

- **Driver registry integration (JEP-0004):** The registry can catalog which driver packages implement which DriverInterfaces, and which ExporterClasses they satisfy, enabling `jmp registry list exporter-classes` and `jmp registry describe exporter-class android-headunit`. The registry could also serve as an alternative source for `inlineDescriptor` resolution, supplementing the inline embedding approach.

- **Capacity planning dashboard:** With `status.satisfiedExporterCount` on every ExporterClass, a web dashboard could show real-time fleet capacity per device profile, utilization rates, and availability trends.

- **ExporterClass-aware scheduling:** The controller's lease scheduler could use ExporterClass satisfaction metadata for smarter scheduling — preferring exporters that satisfy the most optional interfaces, or load-balancing across ExporterClasses with the most available capacity.

- **Test matrix generation:** ExporterClass definitions could drive test matrix generation — automatically running a test suite against every ExporterClass that the test's required interfaces are a subset of.

## Implementation Phases

| Phase | Deliverable | Depends On |
|-------|-------------|------------|
| 0 | Emit `jumpstarter.dev/interface` label in `DriverInstanceReport` (JEP-0001 enhancement) | JEP-0001 |
| 1 | `DriverInterface` CRD definition + operator registration | Phase 0 |
| 2 | `ExporterClass` CRD definition + controller validation on exporter registration (label-based matching) | Phase 1 |
| 3 | `RequestLeaseRequest.exporter_class_name` field + controller lease matching | Phase 2 |
| 4 | `jmp interface publish` + `jmp exporter-class apply/list` CLI tooling | Phase 1 |
| 5 | `jmp validate` local validation tool | Phase 2 |
| 6 | Structural validation via `FileDescriptorProto` comparison (moderate strictness) | Phase 2, JEP-0001 |
| 7 | ExporterClass inheritance (`extends`) | Phase 2 |
| 8 | DriverInterface `compatibleWith` versioning | Phase 1 |

Phases 0–3 are the minimum viable deliverable: named device contracts with controller-enforced lease matching. Phase 4–5 provide developer-facing tooling. Phases 6–8 add the structural depth enabled by JEP-0001's proto introspection.

## Implementation History

- 2026-04-06: JEP drafted as "DeviceClass Mechanism"
- 2026-04-08: Renamed to "ExporterClass Mechanism" (`DeviceClass` → `ExporterClass`, `InterfaceClass` → `DriverInterface`). Replaced CEL selectors with standard Kubernetes label selectors. Changed CRD scope from cluster-scoped to namespace-scoped. Added schema distribution and CLI workflow section. Added `inlineDescriptor` for inline canonical `FileDescriptorProto` storage. Added feasibility assessment based on JEP-0001 PoC analysis. Added schema registry prior art (Buf BSR, Confluent, Envoy). Resolved all "must resolve before acceptance" design questions.

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
