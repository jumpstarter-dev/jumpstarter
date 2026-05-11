# JEP-0015: ExporterClass Mechanism

| Field          | Value                                                      |
| -------------- | ---------------------------------------------------------- |
| **JEP**        | 0015                                                       |
| **Title**      | ExporterClass Mechanism                                    |
| **Author(s)**  | @kirkbrauer (Kirk Brauer)                                  |
| **Status**     | Draft                                                      |
| **Type**       | Standards Track                                            |
| **Created**    | 2026-05-10                                                 |
| **Updated**    | 2026-05-10                                                 |
| **Discussion** | [Matrix](https://matrix.to/#/#jumpstarter:matrix.org)      |
| **Requires**   | JEP-0011 (Protobuf Introspection and Interface Generation) |

---

## Abstract

This JEP introduces an `ExporterClass` custom resource that defines a typed contract between exporters and clients. An ExporterClass specifies required and optional driver interfaces by referencing `DriverInterface` CRDs, enabling the controller to structurally validate exporters at registration time and enabling client-side codegen to produce type-safe device wrappers with named accessors for each interface. An accompanying `DriverInterface` CRD links interface names to their canonical proto definitions and driver packages. Together, these resources bridge the gap between label-based infrastructure selection and typed API contracts.

## Motivation

Today, a client requesting a device lease specifies label selectors — `soc=sa8295p`, `vendor=acme` — and gets an exporter with matching metadata. But labels describe *infrastructure* (what hardware is connected), not *API contracts* (what driver interfaces are available). A client that receives a lease has no guarantee about which driver interfaces the exporter provides. It must call `GetReport`, walk the driver tree, check for the presence of each interface by name, and handle missing interfaces at runtime. This makes it impossible to write type-safe client code in any language.

The consequences are concrete:

- **No compile-time safety.** A test that needs `power.on()`, `serial.connect()`, and `flash.write()` cannot know at compile time whether the leased device provides all three. If the serial driver is missing, the test discovers this at runtime — potentially minutes into a CI pipeline — with an opaque `KeyError` or `AttributeError`.

- **No contract for exporters.** An operator deploying a new exporter for embedded device testing has no way to verify that the exporter's driver configuration satisfies the requirements of the test suites that will lease it. Misconfiguration is discovered when tests fail, not at deployment time.

- **No typed codegen.** Without a formal declaration of which interfaces a device provides, code generation tools cannot produce typed device wrappers with non-nullable accessors. Every interface accessor must be `Optional`, defeating the purpose of typed clients.

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

This proposal introduces four new namespace-scoped Kubernetes CRDs — a **driver-registry** trio (`DriverInterface`, `DriverClient`, `DriverImplementation`) that together describe the interface contracts and their concrete implementations, plus the consumer-facing `ExporterClass` — along with a new `Exporter.spec.exporterClassName` field, a `Lease.spec.exporterClassName` + `exactMatch` pair, and admin CLI tooling.

1. **`DriverInterface`** — the **contract** for a driver interface. Names the interface, carries the canonical proto definition (from JEP-0011) including the build-generated `FileDescriptorProto` descriptor, and — for composite interfaces (e.g., `dutlink`) — declares the named child interfaces every driver implementing it must expose. Authored as a `driver.yaml` partial-CRD manifest committed next to the sibling `.proto` under `interfaces/proto/<package>/`; the build target produces the cluster-applicable CRD with the descriptor filled in. DriverInterface CRDs for all bundled drivers ship as part of the standard Jumpstarter installation.
2. **`DriverClient`** — registers a per-language client-side implementation of a `DriverInterface` (one per `(interface, language)` pair). Carries the language, package name + version, package-registry URL, repo URL, and a language-specific `typeRef` (Python FQN, Java class, Go type, Rust type). Used by `jmp validate client` to verify that a client environment has the right packages installed for a leased exporter's interfaces.
3. **`DriverImplementation`** — registers each concrete driver-side type that implements a `DriverInterface` (e.g., `DutlinkPower`, `MockPower`). One CRD per driver type, with `typeRef`, language, package, description, and source-link metadata. Short alias `driverimpl`.
4. **`ExporterClass`** — a named device profile. Declares a flat list of top-level interface requirements (composite child structure is owned by each referenced `DriverInterface`, not re-declared here), plus standard Kubernetes label selectors, display metadata (`displayName`, `description`, `vendor`, `arch`), `isBase` for abstract base classes, and `extends` for inheritance.
5. **Exporter and Lease field additions** — `Exporter.spec.exporterClassName` lets an exporter declare its primary class (mirroring the K8s `*ClassName` convention used by Pod→RuntimeClass, PVC→StorageClass, Ingress→IngressClass). `Lease.spec.exporterClassName` lets a client request a specific class; `Lease.spec.exactMatch` opts out of subclass matching. The lease controller AND-combines the lease's selector and class name.
6. **`jmp admin` CLI tooling** — `get` / `apply` / `generate` verbs for all four CRDs (`driverinterface`, `driverclient`, `driverimpl`, `exporterclass`), plus user-facing `jmp validate exporter` and `jmp validate client`.

ExporterClass is **transparent to clients that don't want it.** Clients that omit `exporterClassName` continue to request leases by labels exactly as today, with ExporterClass enforcement applied as a backstop (label-matching exporters that fail an applicable class's interface validation are excluded with a descriptive error). Clients that opt in by setting `exporterClassName` get typed selection: "give me a `jetson-orin-nx`" (or any subclass) returns a fully-conformant exporter. Client-side codegen, planned as a future codegen JEP, consumes the registry CRDs to produce typed device wrappers — `device.dutlink.power.on()` accessors whose shape is guaranteed by validation.

### DriverInterface CRD

A `DriverInterface` is the **contract** for a driver interface: it names the interface, carries the canonical proto definition, and — for composite interfaces — declares the child interfaces that any driver implementing this interface must expose. Concrete client and driver implementations are registered separately via the `DriverClient` and `DriverImplementation` CRDs described in the following two sections.

A leaf interface (e.g., `power-v1` — methods only, no nested children):

```yaml
apiVersion: jumpstarter.dev/v1alpha1
kind: DriverInterface
metadata:
  name: jumpstarter-driver-power-v1
  namespace: lab-detroit
spec:
  displayName: Power
  description: |
    Generic power control: on, off, cycle, and live current/voltage readings.

  # Proto definition — canonical identifier and descriptor for this interface
  proto:
    package: jumpstarter.driver.power.v1
    descriptor: CpoBMQpwanVtcHN0YXJ0ZXIvaW50ZXJmYWNlcy9wb3dlci92MS...

  # No children — power is a leaf interface.

status:
  registeredClients: 1
  registeredImplementations: 2
  implementationCount: 15
  conditions:
    - type: Ready
      status: "True"
```

A **composite** interface (e.g., `dutlink-v1` — a debug board with required power/serial/storage child drivers):

```yaml
apiVersion: jumpstarter.dev/v1alpha1
kind: DriverInterface
metadata:
  name: jumpstarter-driver-dutlink-v1
  namespace: lab-detroit
spec:
  displayName: DUT-Link
  description: |
    Multi-function USB-attached debug board exposing power, serial, and storage
    interfaces for a target device.

  proto:
    package: jumpstarter.driver.dutlink.v1
    descriptor: ...

  # Required children of any driver implementing this interface.
  # Each entry names the child accessor slot and the interface it must implement.
  children:
    - name: power           # device.<dutlink-slot>.power
      interfaceRef: jumpstarter-driver-power-v1
      required: true
    - name: serial          # device.<dutlink-slot>.serial
      interfaceRef: jumpstarter-driver-serial-v1
      required: true
    - name: storage         # device.<dutlink-slot>.storage
      interfaceRef: jumpstarter-driver-storage-v1
      required: true
```

The DriverInterface CRD name is a fully-qualified identifier derived from the proto package (e.g., `jumpstarter-driver-power-v1` for `jumpstarter.driver.power.v1`), ensuring uniqueness within a namespace.

`displayName` and `description` are **informational**. They don't affect matching or validation; they exist for admin-console rendering and `kubectl describe` output. Matching is governed entirely by `proto.package`.

The `proto.package` field contains the proto package name (e.g., `jumpstarter.driver.power.v1`) — this is the canonical identifier used for matching. JEP-0011 ships a compiled `FileDescriptorProto` for each driver — produced from the committed `.proto` file at build time — and the exporter exposes that descriptor at runtime via gRPC Server Reflection and `DriverInstanceReport.file_descriptor_proto`. The controller matches a driver in the report tree to a DriverInterface by comparing the driver's `FileDescriptorProto.package` against the DriverInterface's `proto.package`. This eliminates the need for convention-based label matching — the proto package is the canonical identifier.

The `descriptor` field contains the canonical `FileDescriptorProto` as base64-encoded bytes, used by the controller for structural validation. This follows the pattern established by Envoy's gRPC-JSON transcoder, which embeds `FileDescriptorSet` bytes inline in Kubernetes configuration.

#### Why composition lives on `DriverInterface`, not in the `.proto`

A composite driver's children are part of the interface contract — a `dutlink` driver always exposes a `power`, a `serial`, and a `storage` child. But **gRPC services cannot be nested**: a `DutlinkService` in a `.proto` file is a flat list of RPCs and cannot syntactically declare "this service contains a `PowerService` and a `SerialService`". So composition has to live somewhere other than the `.proto` itself.

JEP-0015 puts it on the DriverInterface CRD as `spec.children`. That keeps `proto.descriptor` as the pure method-surface contract (matching JEP-0011's "the `.proto` is the source of truth for methods" rule) and adds a separate, Jumpstarter-level layer for the composition contract — which is exactly what it is. The two pieces of the interface contract are:

- `spec.proto.descriptor` — what methods this interface exposes (canonical, polyglot, parseable by any protobuf tool).
- `spec.children` — what nested interfaces this interface must additionally expose at runtime as named child slots (Jumpstarter-specific, K8s-resolvable, ignored by polyglot proto tooling).

Each `children[*]` entry has three fields: `name` (the accessor slot — composes into paths like `device.dutlink.power.on()`), `interfaceRef` (a reference to another `DriverInterface` by name), and `required` (whether this child must be present for a driver of this interface to be considered conformant). Children can themselves be composite, so this is a recursive structure that defines the full subtree under a driver of this interface.

**Runtime matching of children to declared slots.** At runtime, each driver's `DriverInstanceReport` already carries a `jumpstarter.dev/name=<slot>` label populated by Jumpstarter's `Driver.report()` method when a child is wired in via `self.children["<slot>"] = …`. This is the existing convention — it's not new to this JEP. ExporterClass validation matches a `DriverInterface.spec.children[*].name` slot to the runtime child whose `jumpstarter.dev/name` label equals that value AND whose `fileDescriptorProto.package` matches the slot's referenced interface package. Strict double-match guarantees the slot is filled by the right interface, not just any child happening to be named `power`.

The DriverInterface CRD is **strictly the contract** — proto methods, composition, and display metadata. It deliberately does **not** carry an embedded list of client or driver implementations. Each concrete client (per language) is registered as its own `DriverClient` CRD; each concrete driver-side type is registered as its own `DriverImplementation` CRD. This split gives every implementation a stable Kubernetes name (suitable for permalinks, RBAC scoping, and independent versioning) and keeps the contract object small enough that controllers and admin consoles can list and diff it cheaply.

**Method-level metadata is not in the CRD.** Admin consoles, codegen, and CLIs render method names, request/response types, streaming semantics, and doc comments directly from `proto.descriptor` — specifically the `source_code_info` field for doc comments, per JEP-0011's contract. The descriptor is the single source of truth for the method surface; nothing about methods is duplicated into CRD fields.

### DriverInterface manifest (source-tree representation)

JEP-0011 establishes `interfaces/proto/<package>/<interface>.proto` as the committed source of truth for an interface's method surface. That handles methods, request/response types, streaming semantics, and doc comments — everything the proto can express. But composition (which child interfaces a composite must expose) **cannot live in the `.proto`**: gRPC services cannot be nested, and protobuf has no native way to declare "a `DutlinkService` always contains a `PowerService`, a `SerialService`, and a `StorageService` as child instances". The same is true for the non-proto display metadata (`displayName`, `description`) that the DriverInterface CRD carries.

JEP-0015 introduces a **companion YAML manifest** named `driver.yaml`, committed alongside each `.proto` to hold all non-proto interface metadata. Every interface directory has exactly one `driver.yaml` (no exceptions — even leaf interfaces ship one for uniformity); the filename is fixed across the tree so build tooling and grep can locate them without per-interface naming logic:

```
interfaces/proto/jumpstarter/driver/dutlink/v1/
  dutlink.proto      # method surface (JEP-0011)
  driver.yaml        # name, composition, display metadata (this JEP)
```

The manifest follows the same versioned shape as all other Jumpstarter config and CRD YAMLs — `apiVersion`, `kind`, `metadata`, `spec` — so it is parseable by the same machinery and survives schema evolution the same way. The committed `driver.yaml` is a **partial `DriverInterface` CRD**: it carries every field a human authors, but **omits `spec.proto.descriptor`**, which is a pure build artifact per JEP-0011's principle that `.proto` files are the source of truth and compiled descriptors are produced at build time. The build target reads the source manifest + sibling `.proto`, compiles the descriptor, and emits a complete CRD into the operator bundle output (Helm chart / OLM bundle / a generated CRDs directory). The source manifest is what humans review and diff; the generated CRD is what gets applied to the cluster.

A leaf interface's manifest:

```yaml
# interfaces/proto/jumpstarter/driver/power/v1/driver.yaml
apiVersion: jumpstarter.dev/v1alpha1
kind: DriverInterface
metadata:
  name: jumpstarter-driver-power-v1
spec:
  displayName: Power
  description: |
    Generic power control: on, off, cycle, and live current/voltage readings.
  proto:
    package: jumpstarter.driver.power.v1            # must match sibling .proto
    # spec.proto.descriptor is intentionally absent here — it is a build artifact,
    # filled in by `make driver-registry` when emitting the cluster CRD.
```

A composite interface's manifest carries the children list:

```yaml
# interfaces/proto/jumpstarter/driver/dutlink/v1/driver.yaml
apiVersion: jumpstarter.dev/v1alpha1
kind: DriverInterface
metadata:
  name: jumpstarter-driver-dutlink-v1
spec:
  displayName: DUT-Link
  description: |
    Multi-function USB-attached debug board exposing power, serial, and storage
    interfaces for a target device.
  proto:
    package: jumpstarter.driver.dutlink.v1
  children:
    - name: power                                   # accessor slot
      interfaceRef: jumpstarter-driver-power-v1     # cluster CRD name (matches another driver.yaml's metadata.name)
      required: true
    - name: serial
      interfaceRef: jumpstarter-driver-serial-v1
      required: true
    - name: storage
      interfaceRef: jumpstarter-driver-storage-v1
      required: true
```

Manifest fields:

- `apiVersion` *(required)* — `jumpstarter.dev/v1alpha1`. Versioned in lock-step with Jumpstarter's other CRDs and config files (`ExporterConfig`, `ClientConfig`, `ExporterClass`, etc.); future schema changes bump the apiVersion the same way.
- `kind` *(required)* — `DriverInterface`. The same kind as the cluster CRD this manifest produces.
- `metadata.name` *(required)* — the **cluster-side DriverInterface CRD name**. Every cross-reference (`spec.children[*].interfaceRef`, `ExporterClass.spec.interfaces[*].interfaceRef`, `DriverClient.spec.interfaceRef`, `DriverImplementation.spec.interfaceRef`) resolves against this value. Default convention is a straight dots→hyphens conversion of the proto package — `jumpstarter.driver.dutlink.v1` becomes `jumpstarter-driver-dutlink-v1`. Authors of out-of-tree drivers may pick any DNS-safe slug, but the dots→hyphens default keeps the cluster identifier visually parallel to the proto package and unambiguous.
- `spec.proto.package` *(required)* — the proto package. Must match the `package` declaration in the sibling `.proto`; the build target validates the pair.
- `spec.proto.descriptor` *(absent in source; filled in by the build)* — base64-encoded `FileDescriptorProto`. The source manifest **does not carry this field**. The build target produces it by running `protoc --descriptor_set_out` against the sibling `.proto` and emits it into the generated cluster CRD (in the operator bundle output), exactly the way JEP-0011 treats compiled descriptors as build artifacts. Keeping it out of source means: no large binary blobs in source diffs, no risk of source/proto drift, no need for a "regenerate before committing" rule on authors. The `.proto` is the single committed source of truth for the method surface.
- `spec.displayName` *(optional)* — short human label.
- `spec.description` *(optional)* — markdown-friendly long description.
- `spec.children` *(optional)* — composition. Each entry has:
  - `name` — the child accessor slot. Composes into accessor paths like `device.dutlink.power.on()` and must match the `jumpstarter.dev/name` label on the runtime child.
  - `interfaceRef` — `metadata.name` of another `driver.yaml` in the source tree (which is the same `metadata.name` on the corresponding DriverInterface CRD in the cluster). Build-time and apply-time validation both check that the referenced name exists.
  - `required` — whether this child must be present for any driver of this interface.

Why references are by `metadata.name` (the cluster CRD name) rather than by proto package:

- It is the canonical cluster-side identifier. Every cross-reference in the system — ExporterClass, DriverClient, DriverImplementation, child slots — resolves against the same `metadata.name`. Carrying it on the source-tree manifest means cross-references are stable, explicit, and don't depend on any "proto-package → CRD-name" naming convention that tooling has to maintain.
- It also keeps cross-referencing uniform: `interfaceRef` means the same thing — "a DriverInterface CRD `metadata.name`" — wherever it appears.

`spec.proto.package` exists for the proto/manifest pairing check at build time; it is not used for cross-references.

#### How the source manifest becomes a cluster CRD

`make driver-registry` (see *Schema Distribution and CLI Workflow*) walks `interfaces/proto/**/driver.yaml`. For each manifest:

1. Locates the sibling `.proto`.
2. Compiles it via `protoc --descriptor_set_out` to produce the `FileDescriptorProto` bytes.
3. Emits a **complete `DriverInterface` CRD** into the operator's bundled-CRDs directory (e.g., `deploy/operator/config/crd/bases/` and `deploy/helm/jumpstarter/charts/jumpstarter-controller/templates/crds/`). The emitted CRD is the source manifest with `spec.proto.descriptor` populated from step 2.

The source `driver.yaml` is **not** rewritten in place. Authors edit the `.proto` and the source manifest; the descriptor lives only in the generated output. CI ensures generated CRDs and source are consistent by re-running `make driver-registry` and `git diff --exit-code`-ing the generated CRDs directory.

The applied artifact is the generated CRD, not the source manifest:

- The operator installs the generated CRDs as part of its Helm chart / OLM bundle — that's how end users get them on the cluster. No `kubectl apply` against `interfaces/proto/...` files.
- For out-of-tree drivers, `jmp admin generate driverinterface ./driver.yaml` reads the source manifest + sibling `.proto`, runs `protoc --descriptor_set_out`, and prints the complete CRD to stdout (ready for `kubectl apply` or `jmp admin apply driverinterface`). Custom-driver authors commit only the source manifest + `.proto` in their repo; the descriptor is regenerated each time they emit the cluster CRD.

Pairing and consistency rules:

- A directory containing a `.proto` must also contain a `driver.yaml`; a directory containing a `driver.yaml` must also contain exactly one `.proto`. Either alone is an error.
- The manifest's `apiVersion` must be a recognized version and the `kind` must be `DriverInterface`.
- The source manifest must **not** carry `spec.proto.descriptor` — it is a generated field only, populated at build time. Manifests with a descriptor field set are rejected to avoid stale-bytes-in-source drift.
- The manifest's `spec.proto.package` must equal the `.proto`'s `package` declaration.
- Every `spec.children[*].interfaceRef` must resolve to some other `driver.yaml` in the tree whose `metadata.name` matches. Cross-file references that don't resolve are errors.
- The manifest's `metadata.name` must be unique across all `driver.yaml` files in the tree (and within a Kubernetes namespace at apply time).

### DriverClient CRD

A `DriverClient` registers a **client-side proxy** implementation for a `DriverInterface` in a single language. Multiple `DriverClient`s per interface coexist — typically one per language. The CRD is what `jmp validate client` checks against when verifying that a test environment has the right client packages installed for a leased exporter's interfaces.

```yaml
apiVersion: jumpstarter.dev/v1alpha1
kind: DriverClient
metadata:
  # Convention: <interface-name>-<language>
  name: jumpstarter-driver-power-v1-python
  namespace: lab-detroit
spec:
  interfaceRef: jumpstarter-driver-power-v1
  language: python
  package: jumpstarter-driver-power
  version: "1.0.0"
  # Language-specific FQN / import path that addresses the client implementation
  typeRef: jumpstarter_driver_power.client:PowerClient
  # Package registry base — PyPI, Maven Central, crates.io, npm, the project's own index
  index: https://pkg.jumpstarter.dev/
  # Source repository
  repoUrl: https://github.com/jumpstarter-dev/jumpstarter-driver-power
  # Direct link to the package's page on its language registry
  packageUrl: https://pypi.org/project/jumpstarter-driver-power/
  # Free-form note shown in admin consoles
  note: Bundled with the Python client distribution.
status:
  conditions:
    - type: Ready
      status: "True"
```

Required fields: `interfaceRef`, `language`, `package`, `version`, `typeRef`. The `typeRef` field carries the language-specific fully-qualified name (or import path) that addresses the client type — e.g.:

| Language | `typeRef` example                                                              |
| -------- | ------------------------------------------------------------------------------ |
| Python   | `jumpstarter_driver_power.client:PowerClient`                                  |
| Java     | `dev.jumpstarter.driver.power.PowerClient`                                     |
| Go       | `github.com/jumpstarter-dev/jumpstarter-driver-power/client.PowerClient`       |
| Rust     | `jumpstarter_driver_power::PowerClient`                                        |

The field name is deliberately language-neutral; the schema is the same across languages, and `language` disambiguates how to interpret `typeRef`.

Optional fields:

- `index` — the base URL of the package registry (`https://pkg.jumpstarter.dev/`, `https://repo.maven.apache.org/maven2/`, `https://crates.io/`, …). Used by package managers, not by humans.
- `repoUrl` — VCS source repository. Language-agnostic.
- `packageUrl` — direct link to the package's page on its registry (PyPI, Maven Central, crates.io, npm, …). Replaces the prior Python-specific `pypiUrl`; the field is named neutrally so every language's package-registry page has somewhere to go.
- `note` — free-form caveat surfaced by admin consoles (e.g., "Bundled with the Python client distribution").

Name convention: `<DriverInterface-name>-<language>` for the metadata name. This gives stable IDs for permalinks — e.g., `/driver-clients/jumpstarter-driver-power-v1-python`.

`jmp validate client` (specified later in this JEP) resolves `DriverClient` CRDs: for each interface present on the leased exporter, it checks that a matching `DriverClient` exists for the current language and that the named `package` is installed at a compatible `version`. The `typeRef` is then imported to confirm the client is actually loadable.

### DriverImplementation CRD

A `DriverImplementation` registers one **concrete driver-side type** that implements a `DriverInterface`. Multiple `DriverImplementation` entries per interface are expected — a real driver plus mocks plus alternative hardware drivers all coexist. The CRD ships with the short alias `driverimpl` so `kubectl get driverimpl` and the `jmp admin ... driverimpl` verbs stay terse.

```yaml
apiVersion: jumpstarter.dev/v1alpha1
kind: DriverImplementation        # short name: driverimpl
metadata:
  # Convention: <interface-name>-<short-id>
  name: jumpstarter-driver-power-v1-dutlinkpower
  namespace: lab-detroit
spec:
  interfaceRef: jumpstarter-driver-power-v1
  language: python
  package: jumpstarter-driver-power
  version: "1.0.0"
  # Language-specific FQN / import path of the concrete driver type
  typeRef: jumpstarter_driver_power.driver:DutlinkPower
  # One-liner shown in admin consoles
  description: Power control via the DUT-Link USB MCU.
  # Direct (deep-linkable) URL to the source code
  sourceUrl: https://github.com/jumpstarter-dev/jumpstarter-driver-power/blob/main/jumpstarter_driver_power/driver.py#L18
status:
  conditions:
    - type: Ready
      status: "True"
```

Required fields: `interfaceRef`, `language`, `package`, `version`, `typeRef`. Same `typeRef` semantics as `DriverClient` — the language-specific FQN of the concrete driver type (Python class, Java class, Go type, Rust type, etc.). `language` disambiguates the syntax.

Optional fields:

- `description` — one-liner suitable for a tooltip or table-row caption.
- `sourceUrl` — direct, deep-linkable URL to the source file (ideally with a line anchor).

Name convention: `<DriverInterface-name>-<short-id>` — the short ID is the lowercased Kubernetes-safe slug of the terminal segment of `typeRef` (`DutlinkPower` → `dutlinkpower`). Collisions across packages are resolved by a `-<package-shortname>` suffix; this is a recommendation, not a hard rule, since admins are free to name their own CRDs as they see fit.

The controller may optionally cross-check a registered `DriverImplementation` against an exporter's `DriverInstanceReport` — flagging exporters that report a driver `typeRef` not registered as a `DriverImplementation`. This catches out-of-band installs and driver-name drift. It is non-fatal (a `Warning` condition only) and is tracked as an optional later-phase deliverable; see the *Implementation Phases* and *Future Possibilities* sections.

### ExporterClass CRD

An `ExporterClass` declares a device profile as a set of selectors and interface requirements:

```yaml
apiVersion: jumpstarter.dev/v1alpha1
kind: ExporterClass
metadata:
  name: jetson-orin-nx
  namespace: lab-detroit
spec:
  # Display fields — informational, not used for matching.
  displayName: NVIDIA Jetson Orin NX
  description: |
    NVIDIA Jetson Orin NX dev kit. Used for AI inference benchmarks and CUDA test suites.
  vendor: NVIDIA
  arch: arm64
  # When true, this class exists only to be extended (an abstract base).
  # The controller does not consider isBase classes as direct lease-match candidates.
  isBase: false

  # Standard Kubernetes label selectors for exporter matching.
  # Each selector must be satisfied by an exporter to match this ExporterClass.
  selector:
    matchLabels:
      device-type: embedded-linux
      soc: tegra-orin
    matchExpressions:
      - key: arch
        operator: In
        values: [arm64]

  # Top-level interface requirements only. Composite interfaces (like dutlink)
  # carry their own child structure on their `DriverInterface.spec.children`,
  # so this list stays flat — it just declares which interfaces must appear
  # at the root of the exporter's driver tree.
  interfaces:
    - name: dutlink                # device.dutlink — composite; its required
      interfaceRef: jumpstarter-driver-dutlink-v1   # children come from the dutlink
      required: true                              # DriverInterface's spec.children
    - name: network                # device.network — leaf
      interfaceRef: jumpstarter-driver-network-v1
      required: false

status:
  satisfiedExporterCount: 12
  # Resolved root-level interface list after `extends`-chain merging. Each
  # entry adds an optional `inheritedFrom` field naming the parent
  # ExporterClass it was inherited from. The full *expected tree* (with
  # composite children expanded) is computed by the controller during
  # validation by walking these entries' `interfaceRef`s and recursively
  # expanding via each DriverInterface's spec.children — it is not stored
  # back into resolvedInterfaces.
  resolvedInterfaces:
    - name: dutlink
      interfaceRef: jumpstarter-driver-dutlink-v1
      required: true
      inheritedFrom: embedded-linux-board
    - name: network
      interfaceRef: jumpstarter-driver-network-v1
      required: false
  conditions:
    - type: Ready
      status: "True"
      reason: "ExportersSatisfied"
      message: "12 exporters satisfy all required interfaces"
```

The `spec` has three groups of fields:

**Display fields** — `displayName`, `description`, `vendor`, `arch`, `isBase`. All informational. `selector` is still the sole matching mechanism; these fields exist for admin-console rendering and `kubectl describe` output. `arch` deliberately duplicates information typically expressed in the selector so the console can show it in a single column without parsing label expressions. `isBase: true` marks an ExporterClass that exists only to be extended — controllers skip `isBase: true` classes when evaluating lease-match candidacy directly, even if their selector would otherwise match.

**`selector`** — standard Kubernetes label selectors (`matchLabels` and `matchExpressions`) that each candidate exporter must satisfy. This uses the same label selector mechanism already used by the lease controller, making it familiar to operators and requiring no additional dependencies.

**`interfaces`** — a flat list of **top-level** interface requirements. Each entry has three fields:

- `name` — the accessor name used in generated client code at the device root (e.g., `device.dutlink`, `device.network`).
- `interfaceRef` — a reference to a `DriverInterface` CRD by name.
- `required` — whether the interface must be present at the root of the exporter's driver tree for an exporter to satisfy this ExporterClass.

**ExporterClass does not re-declare the children of composite interfaces.** A `dutlink` driver always exposes `power`, `serial`, and `storage` children — that fact is part of the `dutlink-v1` interface contract and lives on `DriverInterface(jumpstarter-driver-dutlink-v1).spec.children`. The ExporterClass simply says "I require a `dutlink` at the root"; the controller derives the full required subtree from the referenced DriverInterface during validation. This keeps each interface's composition declared in exactly one place (its own DriverInterface CRD) and keeps ExporterClass authoring trivial — you list what an exporter must expose at the top level, and that's it.

This shape declaration is what makes ExporterClass a *typed API contract*: combined with each DriverInterface's `spec.children`, it produces a fully specified expected tree. The Jumpstarter runtime driver tree (per JEP-0011) is delivered on the wire as a flat list of `DriverInstanceReport` entries linked by `parent_uuid` — the controller reconstructs the runtime tree and then walks it in parallel with the composed *expected* tree during validation. See *Exporter Validation Algorithm* below.

`status.resolvedInterfaces` is the flat root-level interface list after `extends`-chain merging. Each entry mirrors the spec entry (`name`, `interfaceRef`, `required`) and adds an optional `inheritedFrom` field naming the parent ExporterClass it was inherited from. Composite child structure is **not** materialized into `resolvedInterfaces` — it's reachable by following each `interfaceRef`, and the controller composes it on demand during validation.

ExporterClass is purely about typing and selection — it does not include driver-specific configuration. Driver parameters (e.g., power cycle delay, serial baud rate) belong in the exporter's `ExporterConfig` YAML, not in the ExporterClass contract.

### ExporterClass Inheritance

ExporterClasses can extend other ExporterClasses to create specialization hierarchies. Because `spec.interfaces` is flat (composition lives on each DriverInterface), inheritance is a simple flat-list merge by `name`:

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
    - name: dutlink                     # base requires a dutlink at root
      interfaceRef: jumpstarter-driver-dutlink-v1
      required: true
---
apiVersion: jumpstarter.dev/v1alpha1
kind: ExporterClass
metadata:
  name: embedded-linux-board
  namespace: lab-detroit
spec:
  extends: base-device                  # inherits the dutlink requirement
  selector:
    matchLabels:
      device-type: embedded-linux
  interfaces:
    - name: network                     # adds an optional network at root
      interfaceRef: jumpstarter-driver-network-v1
      required: false
```

Merge semantics:

- **Same `name` → same root entry.** The child class's entry refines the parent's. If both declare `interfaceRef`, they must agree (mismatch → `Degraded` condition on the child class). The child may make a `required: false` parent entry `required: true`, but not the reverse.
- **New `name` → additional root entry.** Appended to the resolved list.
- **`extends` is transitive.** Grandparent → parent → child merges left-to-right; child declarations have the highest precedence.

After merging, `status.resolvedInterfaces` is the flat list with `inheritedFrom` annotations. Composite-child structure is not part of this merge: it is owned by each `DriverInterface`, not by the ExporterClass. A test written against `base-device` runs on any managed target whose driver tree has a `dutlink` at the root (which transitively requires the `dutlink-v1` interface's declared children — `power`, `serial`, `storage`). A test against `embedded-linux-board` additionally requires an optional `network` at the root.

The `extends` chain is resolved at ExporterClass reconciliation time, not at lease time. The merged list is cached in `status.resolvedInterfaces`. Circular `extends` chains and `interfaceRef` mismatches at the same `name` are detected during reconciliation and result in a `Degraded` condition on the offending ExporterClass.

### Schema Distribution and CLI Workflow

A key operational question is how `DriverInterface`, `DriverClient`, `DriverImplementation`, and `ExporterClass` CRDs are authored, distributed, and applied to clusters. This JEP defines a distribution model that minimizes operator friction by shipping the driver-registry CRDs as part of the standard Jumpstarter installation.

#### Driver-registry distribution

The three driver-registry CRDs — `DriverInterface`, `DriverClient`, and `DriverImplementation` — are treated as **part of the Jumpstarter platform**, versioned and distributed alongside the operator and bundled drivers:

**Bundled drivers** (power, serial, network, ADB, storage, etc.):
- Driver-registry YAML manifests are generated as part of the standard `make manifests` build process and placed alongside the existing CRDs in:
  - Helm chart: `deploy/helm/jumpstarter/charts/jumpstarter-controller/templates/crds/`
  - Operator config: `deploy/operator/config/crd/bases/`
  - OLM bundle: included automatically via `operator-sdk generate bundle`
- A `make driver-registry` Makefile target orchestrates the generation. It walks `interfaces/proto/**/driver.yaml` and emits cluster-ready CRDs into the operator's bundled-CRDs directories without modifying source. For each manifest:
  1. Locate the sibling `.proto`.
  2. Compile it via `protoc --descriptor_set_out` to produce the `FileDescriptorProto` bytes (kept in memory; not committed).
  3. Emit a complete `DriverInterface` CRD into the bundle directories (`deploy/operator/config/crd/bases/`, `deploy/helm/jumpstarter/charts/jumpstarter-controller/templates/crds/`). The emitted CRD is the source manifest with `spec.proto.descriptor` populated from step 2.
- For each bundled driver package, the same target additionally emits the CRDs that have no source-tree representation:
  - One `DriverClient` per supported language (initially Python; additional languages add entries as the polyglot client matrix grows). The `typeRef` is the language-specific FQN of the client class; `package`/`version`/`index`/`packageUrl`/`repoUrl` come from the package's build metadata.
  - One `DriverImplementation` per concrete driver type discovered in the package — for Python, this is the existing `jumpstarter.drivers` entry-point walk; for other languages, analogous discovery mechanisms apply. Each yields one CRD with `typeRef`, `description` (from the type's docstring), and `sourceUrl` (deep-link into the package's published source tree).
- The source `driver.yaml` files are **never modified in place** by the build. The descriptor lives only in the generated output, exactly the way JEP-0011 frames compiled descriptors as build artifacts (not committed alongside `.proto`s). CI re-runs the build target and `git diff --exit-code`s the generated CRDs directory to catch source/proto/descriptor drift.
- Pairing and consistency rules: every directory containing a `.proto` must also contain a `driver.yaml`, and vice versa; the source manifest must not carry `spec.proto.descriptor` (it's generated-only); the manifest's `spec.proto.package` must equal the proto's `package` declaration; every `spec.children[*].interfaceRef` must resolve to some other `driver.yaml`'s `metadata.name`; `metadata.name` must be unique across the tree. Each of these is a build error if violated. Leaf interfaces still ship a `driver.yaml` (with no `spec.children`); uniformity is the rule.
- This target is called by `make manifests` so the driver-registry CRDs are always in sync with the driver code.
- When Jumpstarter is upgraded, all three CRD kinds are updated alongside the operator to reflect interface and implementation changes in the new version.

**Third-party/custom drivers:**
- Custom driver packages ship their own driver-registry YAMLs (e.g., under `k8s/driverinterface.yaml`, `k8s/driverclient.yaml`, `k8s/driverimpl.yaml`).
- Administrators generate the YAMLs using `jmp admin generate driverinterface`, `jmp admin generate driverclient`, and `jmp admin generate driverimpl`, and apply them with the corresponding `jmp admin apply ...` verbs.

This approach ensures that installing Jumpstarter automatically provides the driver-registry CRDs for all bundled drivers, making it seamless for administrators to start using ExporterClasses without manual schema publishing.

#### Admin CLI commands

All cluster management operations for the driver-registry CRDs and ExporterClass live under the `jmp admin` subcommand, following the kubectl-style `verb noun` pattern:

```bash
# DriverInterface (contract)
jmp admin get driverinterfaces
jmp admin apply    driverinterface custom-driver-interface.yaml
jmp admin generate driverinterface jumpstarter_driver_custom.driver.CustomInterface > custom-v1.yaml

# DriverClient (per-language client implementation)
jmp admin get driverclients
jmp admin apply    driverclient   custom-client.yaml
jmp admin generate driverclient   jumpstarter_driver_custom.client:CustomClient > custom-client.yaml

# DriverImplementation (concrete driver-side type) — short alias: driverimpl
jmp admin get driverimpls
jmp admin apply    driverimpl     custom-driver.yaml
jmp admin generate driverimpl     jumpstarter_driver_custom.driver:CustomDriver > custom-driver.yaml

# ExporterClass
jmp admin get exporterclasses
jmp admin apply    exporterclass  embedded-linux-board.yaml
```

Exporter validation is a user-facing command under the top-level `jmp` CLI:

```bash
# Validate an exporter config against all matching ExporterClasses
jmp validate exporter /etc/jumpstarter/exporters/my-exporter.yaml
```

The command loads the exporter configuration, introspects its driver tree to build `DriverInstanceReport` data, and calls the `ValidateExporter` RPC on the controller. The controller resolves which ExporterClasses match the exporter's labels and validates the interface requirements, returning the results. This works through the existing Jumpstarter controller API using the exporter's credentials — no direct Kubernetes cluster access is required.

Client-side validation is also available:

```bash
# Validate that installed client packages match the server's DriverClient registry
jmp validate client
```

The `jmp validate client` command calls the `GetExporterClassInfo` RPC using the client's credentials, then checks the locally installed driver client packages against the `DriverClient` CRDs that apply to the leased exporter's interfaces. For each interface, it verifies:

- A `DriverClient` exists for the current language (e.g., `language: python`).
- The package named in `DriverClient.spec.package` is installed at a version compatible with `DriverClient.spec.version`.
- The `typeRef` is importable / resolvable in the current language runtime.

This catches mismatches before a test runs — e.g., a CI environment missing a required driver package, or running a stale version that doesn't match the server's DriverClient registry.

#### Workflow

1. **Install Jumpstarter** — the operator installation includes `DriverInterface`, `DriverClient`, and `DriverImplementation` CRDs for all bundled drivers. No additional steps needed for standard interfaces.
2. **(Optional) Custom drivers** — for third-party drivers, run `jmp admin generate driverinterface`, then `jmp admin generate driverclient` (per language), then `jmp admin generate driverimpl` (per concrete driver type), and apply the YAMLs.
3. **Author ExporterClass** — a lab admin writes an ExporterClass YAML referencing the installed DriverInterfaces by name.
4. **Apply ExporterClass** — `jmp admin apply exporterclass` or `kubectl apply` registers the ExporterClass.
5. **Validate** — the controller validates exporters against ExporterClasses at registration time. Operators can also pre-validate with `jmp validate exporter <path>`, which calls the controller's `ValidateExporter` RPC using the exporter's existing credentials.

### Controller Validation Flow

#### On exporter registration (`Register` RPC)

Registration is **never rejected** due to ExporterClass non-compliance — the exporter is always accepted and its `DriverInstanceReport` tree is stored in `ExporterStatus.Devices`. This ensures backward compatibility: exporters that predate ExporterClass continue to register and serve label-only leases without disruption.

After storing the device reports, the controller computes the exporter's ExporterClass membership in two passes:

1. **Receive** the exporter's `spec.exporterClassName` (if any), labels, and `DriverInstanceReport` tree (which includes `file_descriptor_proto` from JEP-0011).

2. **Declared-class validation.** If `spec.exporterClassName` is set, the controller resolves that ExporterClass and runs the two-check validation algorithm (defined in detail in *Exporter Validation Algorithm* below):
   a. **Check A — tree completeness.** For each `required: true` interface entry, resolve the referenced `DriverInterface` and walk the `DriverInstanceReport` tree depth-first to find a driver whose `file_descriptor_proto.package` matches the DriverInterface's `proto.package`. If any required interface has no matching driver anywhere in the tree, the check fails for that interface and the missing interface name is recorded.
   b. **Check B — structural compatibility.** For each driver found in Check A, compare its `file_descriptor_proto` against the DriverInterface's canonical `proto.descriptor`: method names, request/response message field types, and streaming semantics must match. If a driver is present but its descriptor doesn't match, the specific mismatch is recorded.
   c. Optional (`required: false`) interfaces run the same Check A + Check B logic but contribute only informational status; they never block satisfaction.
   d. **If both Check A and Check B pass for every required interface**, the declared class **and all of its ancestors via `extends`** are added to `status.satisfiedExporterClasses` (declared class first, then ancestors in extends-chain order). An `ExporterClassCompliance: True` condition is recorded.
   e. **If either Check A or Check B fails for any required interface**, an `ExporterClassCompliance: False` condition records the specific failures (missing interfaces from Check A and descriptor mismatches from Check B, with separate reasons so the operator can tell "missing driver" from "wrong driver version"). The declared class is **not** added to `satisfiedExporterClasses`. The exporter remains registered and remains eligible for label-only leases.

3. **Inferred-class membership.** For every other ExporterClass in the namespace whose `selector` matches the exporter's labels and is not already in `satisfiedExporterClasses` from step 2, the same interface validation runs. Classes that pass are appended to `satisfiedExporterClasses`. This step captures the case where an exporter happens to satisfy a class without declaring it (useful for inheritance hierarchies authored after the exporter was deployed). An `ExporterClassMembership` informational condition lists these inferred classes.

4. Each ExporterClass's `status.satisfiedExporterCount` is updated.

This "accept and flag" approach means:
- Exporters always register successfully — no disruption to existing workflows.
- The declared `exporterClassName` is the authoritative source of identity. Inferred memberships are additive and informational; they never override the declared class.
- Administrators see clear feedback on the Exporter resource about declared-class compliance (`ExporterClassCompliance`) and inferred memberships (`ExporterClassMembership`).
- The exporter remains available for label-only leases even if it fails ExporterClass validation for its declared class.
- `kubectl describe exporter <name>` shows compliance status at a glance.

Example status on a fully-compliant Jetson exporter:

```yaml
spec:
  exporterClassName: jetson-orin-nx
status:
  satisfiedExporterClasses:
    - jetson-orin-nx          # declared
    - embedded-linux-board    # via extends
    - base-device             # via extends
  conditions:
    - type: ExporterClassCompliance
      status: "True"
      reason: "DeclaredClassSatisfied"
      message: "Declared class 'jetson-orin-nx' is fully satisfied."
```

Example condition on an exporter whose declaration doesn't match its actual driver tree — note that the failures from Check A (missing interface) and Check B (descriptor mismatch) are reported side by side in one condition so the operator can see both fix-paths at once:

```yaml
spec:
  exporterClassName: embedded-linux-board
status:
  satisfiedExporterClasses: []
  conditions:
    - type: ExporterClassCompliance
      status: "False"
      reason: "MultipleFailures"
      message: >-
        Declared class 'embedded-linux-board' is not satisfied.
        Check A (tree completeness): missing required interface 'serial-v1'
        — no driver in the report tree has FileDescriptorProto.package
        'jumpstarter.driver.serial.v1'.
        Check B (structural compatibility): interface 'power-v1' is present
        but the driver descriptor doesn't match — missing method 'Read'
        (expected server_streaming rpc Read(ReadRequest) returns (stream
        ReadResponse)).
```

Condition `reason` values for `ExporterClassCompliance: False`:

- `MissingInterface` — Check A failed for one or more required interfaces (no matching driver in the tree).
- `InterfaceStructuralMismatch` — Check B failed for one or more present drivers (descriptor doesn't match canonical).
- `MultipleFailures` — both A and B failures occurred.

#### On lease request

The lease controller filters candidate exporters by AND-combining the lease's `selector`, `exporterClassName`, and `exactMatch` fields:

1. Receive the `LeaseSpec` with its `selector`, optional `exporterClassName`, and optional `exactMatch` flag.

2. Start with all exporters in the namespace as candidates.

3. **If `selector` is set**, filter to exporters whose labels satisfy it (standard `labels.Selector` matching).

4. **If `exporterClassName` is set**:
   a. If `exactMatch: false` (default), filter to candidates where `exporterClassName ∈ status.satisfiedExporterClasses`. This matches the declared class **and any subclass** that extends it, because each exporter's `satisfiedExporterClasses` includes its declared class plus all ancestors.
   b. If `exactMatch: true`, filter to candidates where `spec.exporterClassName == lease.spec.exporterClassName` *exactly*. Exporters that satisfy the class only through inheritance — or that don't declare a primary class — are excluded.

5. **If `selector` is set but `exporterClassName` is not** (legacy/label-only flow), the controller additionally excludes candidates that match an ExporterClass's selector but fail that class's interface validation (the `ExporterClassCompliance: False` case). This preserves the original "label-only leases still benefit from ExporterClass enforcement" guarantee.

6. Bind the lease to the best available remaining candidate.

If no candidate survives the filter, the lease request fails with a descriptive error specifying which dimension excluded the otherwise-matching exporters:

- `exporterClassName` requested but no exporter satisfies it (or, with `exactMatch: true`, no exporter declares it directly).
- `selector` requested but no labels match.
- All label-matching exporters fail ExporterClass compliance — the error references the `ExporterClassCompliance: False` conditions on each.

This gives the client (and the CI pipeline operator) actionable information about why no exporter was available.

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

### Exporter Validation Algorithm

ExporterClass validation runs against the exporter's reconstructed driver tree. The runtime tree arrives on the wire (and is persisted in `ExporterStatus.Drivers`) as a **flat list of `DriverInstanceReport` entries linked by `parentUuid`** — JEP-0011's contract. The controller reconstructs the runtime tree and composes the *expected* tree from the resolved ExporterClass plus each referenced DriverInterface's declared composition, then walks the two in parallel.

**Step 0a — Runtime tree reconstruction**

The controller treats `Drivers` entries with no `parentUuid` as roots and entries whose `parentUuid` matches another entry's `uuid` as children of that parent. The result is one or more rooted trees (typically one, but the protocol does not forbid multiple roots; the algorithm handles either). Reconstruction failures (orphaned `parentUuid`, cycles) surface as `DriverTreeInvalid` conditions and short-circuit the rest of validation.

**Step 0b — Expected-tree composition**

The controller composes the expected tree from the resolved ExporterClass and the driver-registry CRDs:

1. Start from `ExporterClass.status.resolvedInterfaces` — the flat list of required and optional root-level entries (after `extends`-chain merging).
2. For each entry, resolve its `interfaceRef` to the matching `DriverInterface` CRD and append the DriverInterface's `spec.children` as that entry's children.
3. Recursively expand each child entry the same way: resolve its `interfaceRef` and expand its DriverInterface's `spec.children`, and so on, until every interface is either a leaf (no children declared) or a composite whose composition has been fully materialized.

Cycles in `interfaceRef` (interface A declares interface B as a child, B declares A) and unresolved `interfaceRef`s are detected here and produce a `Degraded` condition on the offending DriverInterface or ExporterClass.

The result is the **expected tree** — a fully specified shape with `name` accessor labels at every level. This expected tree is what Check A walks. The expected tree is recomputed when the ExporterClass, any referenced DriverInterface, or the exporter's `Drivers` changes; it is not persisted on the ExporterClass itself.

ExporterClass validation then runs **two independent checks** against the runtime tree, both of which must pass for every required position before the class is considered satisfied. Failing either check produces a non-compliance condition; the two failure modes are distinct and reported separately.

**Check A — Required drivers present at the declared position (tree-shape match)**

For each node in the composed *expected* tree, the controller looks for a matching driver **at the corresponding relative position** in the reconstructed runtime tree:

- **Root entries** in the expected tree must match a driver at the root of the runtime tree.
- **Child entries** (declared via a DriverInterface's `spec.children`) must match a **direct child** of the driver that matched their parent. The match is positional and strict — a descendant two levels deep does *not* satisfy a child entry; only a direct child does.

A child match requires **two conditions** at once: the runtime child's `fileDescriptorProto.package` equals the expected entry's referenced `DriverInterface.spec.proto.package`, AND its `labels["jumpstarter.dev/name"]` equals the expected entry's `name`. The `jumpstarter.dev/name` label is already populated by Jumpstarter's `Driver.report()` from the parent driver's `self.children["<slot>"]` slot key — it is not a new mechanism this JEP introduces. Requiring both an interface-package match and a slot-name match unambiguously fills each declared child slot, even when a parent has multiple children of the same interface (e.g., two `power-v1` children named `usb-power` and `aux-power`).

Root-level matches require only the interface-package match (roots don't have a parent slot to name).

JEP-0011's build pipeline preserves the `.proto` `package` declaration verbatim in the compiled descriptor, so the package match is a simple string comparison. The controller's parsed-and-cached `Drivers[*].interfacePackage` field carries this value to avoid re-parsing on every evaluation.

If a `required: true` entry has no matching driver at its declared position, Check A fails for that entry and the missing position is recorded — including the full accessor path (e.g., `device.dutlink.serial`) so operators see exactly where the shape diverges. A missing parent causes its required children to be reported as **transitively missing** (with a single root-cause line for the parent and per-child lines that name the parent as the root cause) so the message is actionable without being noisy.

**Extra drivers in the runtime tree are allowed.** The expected tree declares the *minimum required shape*; an exporter that ships additional drivers not mentioned by any expected-tree node — e.g., monitoring, debug, or telemetry drivers — still satisfies the class as long as every declared position has a matching driver. The same is true of drivers at unexpected positions: their presence does not invalidate matches elsewhere in the tree.

**Check B — Descriptor signatures match the canonical interface descriptor (structural compatibility)**

For each `(declared-position-entry, matched-driver)` pair from Check A, the controller compares two `FileDescriptorProto` instances:

1. The **canonical** descriptor from the `DriverInterface` CRD's `proto.descriptor` field — the reference schema for the interface at that version.
2. The **driver's actual** descriptor from its `Drivers[*].fileDescriptorProto` — the schema the driver implementation declares.

In the Experimental phase, structural compatibility requires:

- Every RPC method named by the canonical descriptor exists in the driver's descriptor (same `rpc Name` in the same proto service).
- For each method, the input and output message types are present and their field names + proto types match (e.g., `OnRequest { float voltage = 1; }` on both sides).
- Streaming semantics (`server_streaming`, `client_streaming`) match exactly — a streaming method cannot be implemented as unary or vice versa.

Field-number comparison is deferred to the Stable phase. Because both descriptors flow from the same JEP-0011 build pipeline (the same `.proto` files compiled by the same `protoc`), field numbers are authoritative on the proto side once that pipeline stabilizes — strict comparison can be enabled then without false-positive churn against in-development drivers.

A driver that's *present at the declared position* (passes Check A) but whose descriptor *doesn't match* the canonical one (fails Check B) is just as much a failure as a missing driver — both produce non-compliance. The condition message names the specific structural mismatch and the accessor path (e.g., "`device.dutlink.power`: missing method `read`; expected `server_streaming` rpc"), distinguishing it from a missing-position failure.

**Check C — Optional interfaces (informational only)**

For each `required: false` entry (at any level of the tree), the same Check A and Check B logic runs, but the outcome is reported as informational rather than a failure. Missing optional positions, and optional positions present with structural mismatches, do not prevent the ExporterClass from being satisfied. They are surfaced in the Exporter status (and in `jmp validate exporter` output) for operator visibility.

**Pre-JEP-0011 drivers**

A driver in the tree whose `fileDescriptorProto` is empty (e.g., a legacy driver loaded by an exporter built before JEP-0011's descriptor-in-report path landed) cannot be matched against any DriverInterface by Check A — there's no way to identify which interface it implements. If a required position depends on such a driver, Check A fails for that position as if the driver were absent. Such exporters remain available for label-only leases that don't reference the affected ExporterClass.

**Why two independent checks**

Splitting completeness from structural match keeps the failure messages actionable. "Missing `device.dutlink.serial`" tells the operator to add the serial driver as a child of dutlink. "`device.dutlink.serial` present but missing method `read`" tells them they have the *wrong version* of the serial driver — a different fix (upgrade or rebuild) than installing a new package. A single conflated check would lose that distinction.

**Why strict positional matching (rather than "anywhere in the tree")**

The shape itself is part of the contract. A test that writes `device.dutlink.serial.connect()` expects the serial driver to be the `dutlink`'s child, not someone else's. If the same interface package appears at a different position (or twice in the tree at different positions), strict positional matching unambiguously says which one satisfies which declared entry. Codegen for typed device wrappers depends on this: stable `device.dutlink.serial` accessors only exist when the validator guarantees the shape.

### API / Protocol Changes

#### New CRDs

Four new namespace-scoped CRDs are added to the Jumpstarter operator:

- `driverinterfaces.jumpstarter.dev/v1alpha1` — `DriverInterface`
- `driverclients.jumpstarter.dev/v1alpha1` — `DriverClient`
- `driverimplementations.jumpstarter.dev/v1alpha1` — `DriverImplementation` (short alias: `driverimpl`)
- `exporterclasses.jumpstarter.dev/v1alpha1` — `ExporterClass`

All four are namespace-scoped to support multi-tenant lab environments where different teams manage their own device profiles and interface definitions within their namespace. This diverges from the Kubernetes `*Class` convention (where `StorageClass`, `IngressClass`, etc. are cluster-scoped) but better fits Jumpstarter's deployment model, where multiple labs or teams share a single cluster with independent device profiles.

The CRDs themselves are additive. The `Exporter` and `Lease` CRDs each gain a single new optional field (described below) to declare and request an ExporterClass.

#### Modified CRDs

**`Exporter`** gains an optional `spec.exporterClassName` field — the **declared primary class** of the exporter, following the Kubernetes `*ClassName` consumer convention used by `Pod.spec.runtimeClassName`, `PersistentVolumeClaim.spec.storageClassName`, and `Ingress.spec.ingressClassName`:

```go
type ExporterSpec struct {
    // ... existing fields (Labels, Devices, etc.) ...

    // The ExporterClass this exporter declares itself to be. Optional.
    // If set, the controller verifies that the exporter actually satisfies
    // this class's interface requirements and surfaces any mismatch as a
    // condition. Lease requests naming this class match this exporter
    // (subject to subclass matching rules — see below).
    ExporterClassName string `json:"exporterClassName,omitempty"`
}
```

Operators may leave this empty — exporters without a declared `exporterClassName` are still discoverable via label selectors and may still satisfy ExporterClasses through inference (as described in the Controller Validation Flow), but no class is treated as their *primary* identity. This preserves backward compatibility with pre-JEP-0015 exporter manifests.

**`ExporterStatus`** gains a new `Drivers` field — a faithful CRD-side projection of the wire `DriverInstanceReport` tree — and `SatisfiedExporterClasses`:

```go
type ExporterStatus struct {
    // ... existing fields (Conditions, Credential, LeaseRef, LastSeen, Endpoint,
    // ExporterStatusValue, StatusMessage) ...

    // Drivers is the exporter's reported driver tree, persisted as a flat list
    // with parentUuid refs (same shape as the wire protocol's
    // DriverInstanceReport). The controller reconstructs the tree by walking
    // parentUuid refs during validation.
    Drivers []DriverInstance `json:"drivers,omitempty"`

    // Devices is the legacy minimal projection retained for one release for
    // out-of-tree consumers. New code should consume Drivers instead.
    // Deprecated: use Drivers.
    Devices []Device `json:"devices,omitempty"`

    // ExporterClasses that this exporter satisfies, in derivation order:
    // declared class first, then its ancestors via `extends`, then any other
    // classes whose selector matches the exporter's labels and whose
    // required interfaces are all present.
    SatisfiedExporterClasses []string `json:"satisfiedExporterClasses,omitempty"`
}

// DriverInstance mirrors the wire DriverInstanceReport plus two controller-
// computed convenience fields (interfacePackage and interfaceRef) populated
// during validation so consumers don't have to re-parse the descriptor.
type DriverInstance struct {
    // Fields from the wire DriverInstanceReport
    Uuid                string            `json:"uuid"`
    ParentUuid          *string           `json:"parentUuid,omitempty"`
    Labels              map[string]string `json:"labels,omitempty"`
    Description         string            `json:"description,omitempty"`
    MethodsDescription  map[string]string `json:"methodsDescription,omitempty"`
    FileDescriptorProto []byte            `json:"fileDescriptorProto,omitempty"`  // JEP-0011

    // Controller-computed fields populated during validation:
    // InterfacePackage is the proto package name parsed from FileDescriptorProto
    // (e.g., "jumpstarter.driver.power.v1"). Empty if FileDescriptorProto is
    // absent or unparseable.
    InterfacePackage string `json:"interfacePackage,omitempty"`
    // InterfaceRef is the matched DriverInterface CRD name (e.g.,
    // "jumpstarter-driver-power-v1"). Empty if no DriverInterface in the
    // namespace matches InterfacePackage.
    InterfaceRef string `json:"interfaceRef,omitempty"`
}
```

`SatisfiedExporterClasses` is precomputed at registration time (and re-evaluated on relevant updates). It always lists the declared `spec.exporterClassName` first if set, followed by its ancestors via `extends`, followed by any other classes whose selector matches the exporter's labels and whose interface requirements are met. This precomputation is what lets lease-time matching be a simple slice membership check.

`Drivers` replaces the legacy `Devices` field as the canonical projection of the exporter's driver tree. The legacy `Devices []Device` field (which carried only `uuid`/`parent_uuid`/`labels` and was not consumed anywhere in the controller as of this JEP) remains populated for one release as a deprecation courtesy for any out-of-tree consumers; new code in the controller, admin consoles, and CLI must consume `Drivers`. The `parent_uuid` (snake_case) JSON tag on `Device` is preserved as-is on the deprecated `Device` type; `DriverInstance` uses `parentUuid` (camelCase), matching Kubernetes JSON conventions.

The existing `Conditions` slice on `ExporterStatus` is used for several condition types added by this JEP:

- `ExporterClassCompliance` — reports validation failures for the **declared** class (mismatch between `spec.exporterClassName` and the actual driver tree).
- `ExporterClassMembership` — informational status listing all inferred classes the exporter satisfies beyond its declaration.
- `DriverTreeInvalid` — reconstruction failure (orphaned `parentUuid`, cycle in parent refs). Short-circuits ExporterClass validation until the exporter re-registers with a valid tree.

**`Lease`** gains two optional fields in `spec`:

```go
type LeaseSpec struct {
    // ... existing fields (Selector, Duration, Reason, etc.) ...

    // Request an exporter that satisfies this ExporterClass. Optional.
    // AND-combined with Selector — both must match.
    ExporterClassName string `json:"exporterClassName,omitempty"`

    // If true, require an exact match: the exporter's declared
    // exporterClassName must equal ExporterClassName (subclasses are
    // rejected). Default false — any exporter satisfying the class
    // directly or via `extends` is eligible. Has no effect when
    // ExporterClassName is empty.
    ExactMatch bool `json:"exactMatch,omitempty"`
}
```

#### Lease-time interaction between class, selector, and `extends`

The lease controller evaluates candidates by AND-combining all of `selector`, `exporterClassName`, and `exactMatch`:

1. **`selector` only** (no class named) — existing behavior. Candidate exporters are filtered by label selector. ExporterClass enforcement still applies as a backstop: an exporter whose labels match an ExporterClass's selector but which fails that class's interface validation is excluded with a descriptive error (see *Controller Validation Flow*).

2. **`exporterClassName` only** (no selector) — the controller filters to exporters where `exporterClassName ∈ status.satisfiedExporterClasses`. By default (`exactMatch: false`) this matches the declared primary class **and any subclass** that extends it: if a Lease requests `embedded-linux-board`, a `jetson-orin-nx` exporter is a candidate, because `jetson-orin-nx`'s `satisfiedExporterClasses` includes `embedded-linux-board` via `extends`.

3. **Both `exporterClassName` and `selector`** — both conditions must hold. "Give me a Jetson Orin NX in lab=austin."

4. **`exactMatch: true`** — the exporter's declared `spec.exporterClassName` must equal the lease's `exporterClassName` exactly. Subclass-only exporters are excluded. Exporters with no declared class are excluded.

This makes the typical workflow `lease.spec.exporterClassName = "embedded-linux-board"` return any compatible exporter (Jetson Orin NX, RPi 5, etc.), while a test that needs a specific platform sets `exporterClassName = "jetson-orin-nx"` to narrow further.

`Lease.spec.exporterClassName` is the canonical wire field. CLI shortcuts like `jmp lease acquire --selector exporterClass=jetson-orin-nx` are sugar that maps to the canonical field server-side; the selector mechanism is not overloaded with a reserved label key.

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

A `GetExporterClassInfo` RPC is also added, callable by clients to retrieve the ExporterClass and DriverInterface definitions that apply to a leased exporter. This enables clients to discover which interfaces are available, check for missing client packages, and provides the foundation for a future codegen JEP:

```protobuf
rpc GetExporterClassInfo(GetExporterClassInfoRequest) returns (GetExporterClassInfoResponse);

message GetExporterClassInfoRequest {
  string exporter_uuid = 1;
}

message GetExporterClassInfoResponse {
  repeated ExporterClassValidationResult exporter_classes = 1;
  repeated DriverInterfaceInfo           driver_interfaces = 2;
}

message DriverInterfaceInfo {
  string name = 1;                                // e.g., "jumpstarter-driver-power-v1"
  string display_name = 2;                        // e.g., "Power"
  string description = 3;                         // markdown-friendly
  string proto_package = 4;                       // e.g., "jumpstarter.driver.power.v1"
  bytes  descriptor = 5;                          // canonical FileDescriptorProto
  repeated DriverClientInfo         clients = 6;          // matching DriverClient CRDs
  repeated DriverImplementationInfo implementations = 7;  // matching DriverImplementation CRDs
}

message DriverClientInfo {
  string name = 1;                 // DriverClient CRD name, e.g., "jumpstarter-driver-power-v1-python"
  string language = 2;             // e.g., "python", "java", "go", "rust"
  string package = 3;              // e.g., "jumpstarter-driver-power"
  string version = 4;              // e.g., "1.0.0"
  string index = 5;                // e.g., "https://pkg.jumpstarter.dev/"
  string type_ref = 6;             // language-specific FQN, e.g., "jumpstarter_driver_power.client:PowerClient"
  string repo_url = 7;             // optional
  string package_url = 8;          // optional — direct link to the package page on its registry
  string note = 9;                 // optional
}

message DriverImplementationInfo {
  string name = 1;                 // DriverImplementation CRD name
  string language = 2;
  string package = 3;
  string version = 4;
  string type_ref = 5;             // e.g., "jumpstarter_driver_power.driver:DutlinkPower"
  string description = 6;          // optional
  string source_url = 7;           // optional
}
```

The RPC authenticates using the client's credentials. It returns the ExporterClass compliance status and, for each interface in scope, the full set of registered `DriverClient` and `DriverImplementation` entries (mirroring the CRD specs). This lets clients verify they have the correct driver packages installed and gives admin consoles enough data to render the prototype's per-interface "Client implementations" and "Driver classes" surfaces from a single RPC. The detailed design of client-side codegen is deferred to a future codegen JEP. Broader discovery RPCs (`ListDriverInterfaces`, `ListExporterClasses`, etc., scoped to a namespace rather than a single lease) are out of scope for this JEP and tracked in **JEP-0014 (Admin API)**.

#### Modified CLI

New admin subcommands are added following the kubectl-style `verb noun` pattern: `jmp admin get/apply/generate driverinterface`, `jmp admin get/apply/generate driverclient`, `jmp admin get/apply/generate driverimpl` (short alias for `driverimplementation`), `jmp admin get/apply exporterclass`. New user-facing commands `jmp validate exporter <path>` (exporter validation) and `jmp validate client` (client package validation) are added. No existing CLI commands are modified.

### Discovery and the Admin API

All five resource kinds introduced or referenced by this JEP — `ExporterClass`, `DriverInterface`, `DriverClient`, `DriverImplementation`, and the existing `Exporter` — are Kubernetes CRDs. Any K8s-aware client (kubectl, K8s client SDKs, web consoles that speak the Kubernetes API directly) browses them through the standard Kubernetes API surface and gets watch/list/get semantics for free.

For non-K8s consumers — including the polyglot admin console targeted by the OpenShift Console prototype — a gRPC discovery surface (list/get RPCs scoped to a namespace rather than a single lease) is required. That surface is the subject of **JEP-0014 (Admin API)** and is out of scope here. JEP-0015 guarantees the CRD schemas above are the canonical shape; JEP-0014's discovery RPCs return the same data.

`GetExporterClassInfo` (defined above) is the one per-lease query specified by this JEP. It exists because a leased client needs ExporterClass + DriverInterface + DriverClient + DriverImplementation metadata for *its* exporter without having (or needing) Kubernetes API access.

### Hardware Considerations

This JEP is a control-plane change. No hardware is required or affected. ExporterClass and DriverInterface are Kubernetes CRDs processed by the controller. The validation logic operates on `DriverInstanceReport` metadata and `FileDescriptorProto` descriptors — it does not interact with physical devices or timing-sensitive operations.

The `jmp validate` tool loads exporter configurations and introspects driver classes but does not initialize hardware. It runs on the operator's workstation, not on the exporter host.

## Design Decisions

The following design decisions were made during the development of this JEP. Each documents the alternatives considered and the rationale for the chosen approach. Future contributors evaluating changes or extensions should refer back here for context on why the design is the way it is.

### DD-1: Namespace-scoped CRDs

**Alternatives considered:**

1. **Cluster-scoped** — follow the Kubernetes `*Class` convention (`StorageClass`, `IngressClass`, `RuntimeClass` are cluster-scoped resources owned by cluster admins).
2. **Namespace-scoped** — every CRD this JEP introduces (`DriverInterface`, `DriverClient`, `DriverImplementation`, `ExporterClass`) lives in a namespace, matching the existing Jumpstarter CRDs.

**Decision:** Namespace-scoped.

**Rationale:** The Jumpstarter controller has been **single-namespace-only since 0.8.0** — `controller/cmd/main.go` configures the manager's cache to watch exactly one namespace (the controller's own, auto-detected from the service-account namespace file or set via `NAMESPACE`) and exits with an error if no namespace is configured. All existing Jumpstarter CRDs (`Exporter`, `Lease`, `Client`, `ExporterAccessPolicy`) declare `scope: Namespaced`. Multi-tenancy across multiple labs or teams in a single Kubernetes cluster is achieved by deploying multiple controller instances, one per namespace, each reconciling only its own resources.

Cluster-scoped CRDs for JEP-0015's resources would be the lone outliers in this model: they would not be reconciled by any single-namespace controller (each controller would have to reach across namespaces to read them), and references from namespace-scoped resources (e.g., `ExporterClass.spec.interfaces[*].interfaceRef` pointing at a `DriverInterface`) would cross the namespace boundary — breaking RBAC isolation between tenants and inviting accidental cross-tenant coupling.

Namespace-scoping is therefore the only choice consistent with the controller's deployment shape. The cost — divergence from the K8s `*Class` precedent for cluster-wide infrastructure resources (`StorageClass`, `IngressClass`) — is structural: that precedent is shaped by resources genuinely shared across an entire cluster (one CSI driver per cluster, one ingress controller per cluster), whereas Jumpstarter's driver inventory and device profiles are tenant-scoped by design.

### DD-2: Standard Kubernetes label selectors (not CEL)

**Alternatives considered:**

1. **CEL expressions** — Common Expression Language as the selector mechanism, supporting boolean logic and field access beyond simple key/value matching.
2. **`matchLabels` + `matchExpressions`** — standard Kubernetes `labels.Selector` matching, the same shape used elsewhere in the Jumpstarter controller and across the K8s ecosystem.

**Decision:** Standard label selectors.

**Rationale:** The selector mechanism is already in use in the lease controller; reusing it requires no new dependencies and no new mental model for operators. CEL would add a direct `cel-go` dependency for a use case (label matching) where the existing primitives are sufficient. CEL remains a future enhancement (see *Future Possibilities*) if a concrete need for richer expressions appears.

### DD-3: Implementation registry split into three CRDs

**Alternatives considered:**

1. **One CRD per implementation kind** — `DriverInterface` (contract) + `DriverClient` (per language) + `DriverImplementation` (per concrete driver type). Cross-references by stable Kubernetes name.
2. **Single `DriverInterface` with embedded `drivers[]` array** — the original draft. One CRD per interface, listing all client and driver implementations inline.
3. **One `DriverPackage` CRD per package** — coarser grain. Each package gets one CRD that lists every interface it implements, each interface's client, and each driver class.

**Decision:** Three CRDs (Option 1).

**Rationale:** Embedding implementations in `DriverInterface` made the CRD large, mixed the contract with the registry, and forced per-class identifiers (FQNs) to be addressed by array position rather than as Kubernetes names. The three-CRD split gives every implementation a stable Kubernetes name suitable for permalinks, RBAC scoping, and independent versioning — and aligns with the K8s pattern of separating a contract (`StorageClass`, `IngressClass`) from a driver registration (`CSIDriver`). One-CRD-per-package muddles concerns when a package implements many interfaces. Per-class granularity for `DriverImplementation` lets admin consoles deep-link each driver type independently.

### DD-4: Composition lives on `DriverInterface`, not on `ExporterClass`

**Alternatives considered:**

1. **Composition on `DriverInterface`** — `DriverInterface.spec.children` declares which child interfaces a composite must expose. ExporterClass references only root-level interfaces; the controller expands the expected tree from the registry.
2. **Composition on `ExporterClass`** — `ExporterClass.spec.interfaces[*].children` declares the full tree shape per profile.

**Decision:** Composition on `DriverInterface`.

**Rationale:** A composite's children are part of the interface contract — every `dutlink` driver always exposes `power`, `serial`, `storage`. Declaring that fact once on `DriverInterface.spec.children` keeps it in a single canonical place; declaring it on every ExporterClass that references `dutlink` would repeat it. ExporterClass becomes a flat list of top-level requirements ("I need a `dutlink` at the root"), and the controller composes the expected tree by recursively expanding each `interfaceRef` via the referenced DriverInterface's children. This also matches how composite drivers naturally model their structure in code (`self.children["power"] = …`).

### DD-5: Source-tree manifest as a `driver.yaml` partial CRD

**Alternatives considered:**

1. **`driver.yaml` partial CRD** — every interface directory under `interfaces/proto/<package>/` ships a `driver.yaml` with the same shape as the cluster `DriverInterface` CRD, omitting only `spec.proto.descriptor` (the build artifact).
2. **Per-interface filename** (`<interface>.yaml`) — same content but filename derived from the interface name.
3. **No source-tree manifest** — express composition via proto custom options or a sidecar protobuf message inside the `.proto`.

**Decision:** `driver.yaml` partial CRD with a fixed filename.

**Rationale:** Composition cannot live in the `.proto` because gRPC services cannot be nested (Option 3 ruled out by the protocol). A separate YAML keeps the contract material reviewable by humans without protobuf tooling. Fixing the filename to `driver.yaml` (vs. per-interface naming) lets build tooling and grep locate manifests without per-interface naming logic. Making the manifest a partial CRD — same `apiVersion`/`kind`/`metadata`/`spec` shape as the cluster artifact — keeps it parseable by the same loaders as the rest of Jumpstarter's YAML kinds (`ExporterConfig`, `ClientConfig`, `ExporterClass`) and minimizes mental translation between source and cluster.

### DD-6: Cross-references use `metadata.name` (cluster CRD name), not proto package

**Alternatives considered:**

1. **By cluster name** — every `interfaceRef` in the system (`children[*].interfaceRef`, `ExporterClass.interfaces[*].interfaceRef`, `DriverClient.spec.interfaceRef`, `DriverImplementation.spec.interfaceRef`) resolves against the same `metadata.name` value (which is also the source manifest's `metadata.name`).
2. **By proto package** — references use the proto package (a universal identifier independent of cluster naming), and the build target maps proto package → cluster name at emit time.

**Decision:** By cluster name.

**Rationale:** Adding an explicit `metadata.name` to each source `driver.yaml` lets the same identifier appear in source, build output, and cluster CRD. No proto-package-to-CRD-name translation step is required, and references stay stable across renames (rename the cluster `metadata.name` and every cross-reference is found by grep). The trade-off is that authors must pick a `metadata.name` — but the default convention (DD-7) makes this trivial.

### DD-7: Default `metadata.name` is a dots→hyphens conversion of the proto package

**Alternatives considered:**

1. **Dots→hyphens** — `jumpstarter.driver.dutlink.v1` → `jumpstarter-driver-dutlink-v1`. Mechanical and unambiguous.
2. **Reverse-DNS prefix** — `dev-<org>-<interface>-<version>` (`dev-jumpstarter-dutlink-v1`). The original placeholder convention.
3. **Free-form** — authors choose whatever DNS-safe slug they prefer; no default.

**Decision:** Dots→hyphens.

**Rationale:** Mechanical conversion keeps the cluster identifier visually parallel to the proto package, which is the canonical interface identity. A human reading either form can identify the other at a glance. Out-of-tree authors retain freedom to pick another DNS-safe slug.

### DD-8: Compiled `FileDescriptorProto` is a build artifact, not committed in source

**Alternatives considered:**

1. **Build artifact only** — `spec.proto.descriptor` is absent from the source `driver.yaml`. The build target reads source + sibling `.proto`, compiles the descriptor, and emits a complete CRD to the operator bundle output. Source is never rewritten in place.
2. **Committed bytes** — `spec.proto.descriptor` is written into the source `driver.yaml` by the build target. `kubectl apply -f driver.yaml` works directly.

**Decision:** Build artifact only.

**Rationale:** This matches JEP-0011's foundational principle: the `.proto` is the committed source of truth; compiled descriptors are build artifacts. Committing the bytes would put large base64 blobs in source diffs, require authors to regenerate-before-commit, and create a stale-bytes-in-source failure mode. The trade-off — losing direct `kubectl apply -f driver.yaml` — is acceptable because end users install CRDs via the operator (Helm/OLM), not by hand; out-of-tree authors use `jmp admin generate driverinterface` to emit a complete CRD for application.

### DD-9: Canonical descriptor stored inline in the cluster CRD (not registry-resolved)

**Alternatives considered:**

1. **Inline `spec.proto.descriptor`** — the cluster CRD carries the descriptor bytes directly. The controller validates against this field with no external lookups.
2. **Registry-based resolution** — the CRD carries only a reference (e.g., a Buf BSR URL) and the controller fetches the descriptor at validation time.

**Decision:** Inline.

**Rationale:** Cluster-local validation should not require network round-trips to an external registry. Envoy's gRPC-JSON transcoder uses the same inline-bytes pattern for the same reason. A future Driver Registry JEP can add registry-based resolution as an additional source if a use case appears, without changing the inline default.

### DD-10: Exporter declares + Lease references class by plain `*ClassName: string`

**Alternatives considered:**

1. **Plain `*ClassName: string` on both** — `Exporter.spec.exporterClassName: string` and `Lease.spec.exporterClassName: string`, matching `Pod.spec.runtimeClassName`, `PVC.spec.storageClassName`, `Ingress.spec.ingressClassName` exactly.
2. **Typed `exporterClassRef: {name}` on both** — `LocalObjectReference`-style structured field on both. More extensible.
3. **Asymmetric** — typed ref on Exporter (provider declares its identity formally), string name on Lease (consumer follows K8s convention).
4. **Reserved selector key** — recognize `jumpstarter.dev/exporterclass=<name>` as a virtual label in the existing selector; no new field.

**Decision:** Plain `*ClassName: string` on both.

**Rationale:** This matches the dominant K8s pattern for class consumers and avoids the asymmetry of Option 3. The reserved-selector approach (Option 4) overloads the selector mechanism with virtual labels, which mixes selection by labels with selection by class identity and creates discoverability issues. Plain strings keep the field surface minimal and matchable in YAML by grep.

### DD-11: Lease class match defaults to subclass-friendly, opt-out via `exactMatch: true`

**Alternatives considered:**

1. **Subclass-friendly default** — a `Lease.spec.exporterClassName: embedded-linux-board` matches an exporter whose declared class is `jetson-orin-nx` (which extends `embedded-linux-board`). Add `Lease.spec.exactMatch: true` to require exact equality.
2. **Strict default** — only exporters whose declared class is exactly the named class match; subclass-friendly matching requires opt-in.

**Decision:** Subclass-friendly default.

**Rationale:** Inheritance has no end-to-end value if the natural request form ("give me anything `embedded-linux-board`-compatible") doesn't honor it. Strict default would require clients to enumerate every subclass to express a category — defeating the purpose of `extends`. `exactMatch: true` exists for the narrow case where a test must run on a specific platform.

### DD-12: Validation is two independent checks — tree completeness, then structural compatibility

**Alternatives considered:**

1. **Two independent checks** — Check A walks the composed expected tree and verifies every required position has a matching driver at the right relative position in the runtime tree (tree completeness). Check B compares each matched driver's `FileDescriptorProto` against the canonical `DriverInterface.spec.proto.descriptor` (structural compatibility). Both must pass; failure reasons are distinct (`MissingInterface`, `InterfaceStructuralMismatch`, `MultipleFailures`).
2. **Single conflated check** — combine the two into one "matches the expected interface" verdict per position.

**Decision:** Two independent checks.

**Rationale:** "Missing the serial driver" and "wrong version of the serial driver" require different fixes (install vs. upgrade). Conflating them into a single check loses that distinction in failure messages — operators get an opaque "doesn't match" verdict and have to dig. Keeping the checks separate keeps the failure messages actionable.

### DD-13: Strict positional matching in tree validation

**Alternatives considered:**

1. **Strict positional** — a required child at `device.dutlink.power` must be a *direct* child of the matched `dutlink` driver, identified by the existing `jumpstarter.dev/name` label populated by `Driver.report()`. A power driver elsewhere in the tree does not satisfy this position.
2. **Loose (anywhere-under)** — any descendant of the matched parent matches.
3. **Flat** — ignore the parent-child structure; only check whether the interfaces exist anywhere in the runtime tree.

**Decision:** Strict positional.

**Rationale:** Codegen for typed device accessors (`device.dutlink.power.on()`) only works when the validator guarantees the shape. Loose matching would allow a power driver at the root to satisfy a declared `dutlink → power` slot, breaking the accessor expectation. Flat matching loses the contract entirely. The runtime carries the slot name in the `jumpstarter.dev/name` label already, so the position match has no infrastructure cost.

### DD-14: Moderate structural strictness in Experimental, strict in Stable

**Alternatives considered:**

1. **Moderate for Experimental** — method names, request/response message field types, and streaming flags must match. Field-number comparison deferred to Stable.
2. **Strict from day one** — also compare field numbers.

**Decision:** Moderate for Experimental, with field numbers added in Stable.

**Rationale:** Both descriptors flow from the same JEP-0011 build pipeline (same `.proto`, same `protoc`), so field numbers are authoritative on the proto side once the pipeline stabilizes. Comparing them strictly while the pipeline is still in flux would produce false-positive churn against in-development drivers. Adding strict field-number comparison in Stable is straightforward once the pipeline ships.

### DD-15: Language-neutral implementation field names (`typeRef`, `packageUrl`)

**Alternatives considered for the implementation identifier:**

1. **`typeRef: string`** — language-specific FQN/import path (Python `module:Class`, Java `package.Class`, Go `package.Type`, Rust `module::Type`).
2. **`classFqn: string`** — same content, but the field name presumes the implementation is a "class". Awkward in Rust/Go where the natural form is a type/struct/function.
3. **Per-language split fields** — `python: {module, class}`, `go: {package, symbol}`, etc.

**Alternatives considered for the package-registry URL:**

1. **`packageUrl: string`** — language-agnostic direct link to the package on its registry (PyPI, Maven Central, crates.io, npm, …).
2. **`pypiUrl: string`** — Python-specific name; awkward for non-Python languages.

**Decision:** `typeRef` and `packageUrl`.

**Rationale:** The CRDs are shared across all languages; the field names must be too. `typeRef` reads naturally for any addressable type or import path, and `language` disambiguates how to interpret the value. `packageUrl` similarly accommodates every registry without leaking Python-specific vocabulary.

### DD-16: Discovery / browse RPCs deferred to JEP-0014 (Admin API)

**Alternatives considered:**

1. **Defer to JEP-0014** — namespace-scoped list/get RPCs (`ListExporterClasses`, `ListDriverInterfaces`, etc.) are out of scope; this JEP defines only the per-lease `GetExporterClassInfo` RPC that a leased client uses to discover its own context.
2. **Define list/get RPCs here** — add the full namespace-scoped discovery surface in JEP-0015.

**Decision:** Defer to JEP-0014.

**Rationale:** JEP-0014 is the Admin API JEP and is the natural home for namespace-scoped discovery RPCs. Defining them here would either duplicate that work or pre-empt JEP-0014's design space. The per-lease `GetExporterClassInfo` is in scope here because it is specific to the lease workflow this JEP introduces.

## Design Details

### Architecture

```text
┌────────────────────────┐     ┌────────────────────────┐
│   DriverInterface CRD  │     │   DriverInterface CRD  │
│  jumpstarter-driver-      │     │  jumpstarter-driver-      │
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

Implementation of this JEP depends on JEP-0011 (Protobuf Introspection and Interface Generation) being merged and shipped, since the descriptor-in-report path and the committed `.proto` files are JEP-0011's contributions. No proof-of-concept for the ExporterClass / DriverInterface CRD path currently exists; the work described under *Implementation Phases* below is greenfield. Specific items required from JEP-0011 — `FileDescriptorProto` carried in `DriverInstanceReport`, gRPC Server Reflection at the exporter, and `.proto` files committed alongside each driver package — are tracked in that JEP's own deliverables.

## Test Plan

### Unit Tests

- **Tree reconstruction:** Verify that a flat `Drivers` list with valid `parentUuid` refs reconstructs to the expected rooted tree. Verify that orphaned `parentUuid` and cycles produce a `DriverTreeInvalid` condition and short-circuit further validation.
- **DriverInterface matching:** Verify that the controller correctly identifies drivers in the reconstructed tree by parsing `FileDescriptorProto.package` (cached in `Drivers[*].interfacePackage`) and matching it against each `DriverInterface.spec.proto.package`. Confirm `Drivers[*].interfaceRef` is populated with the matched CRD name.
- **Expected-tree composition:** Verify that the controller composes the expected tree by walking each `ExporterClass.status.resolvedInterfaces` entry, resolving its `interfaceRef` to a DriverInterface, and recursively expanding `DriverInterface.spec.children`. Cover: a flat ExporterClass referencing only leaf interfaces; an ExporterClass referencing a composite (`dutlink`) whose DriverInterface declares `power`/`serial`/`storage` children; a two-level deep composition.
- **Composition cycle detection:** Verify that a DriverInterface chain forming a cycle in `spec.children` (A → B → A) is detected during composition and produces a `Degraded` condition on the offending interface.
- **Composition unresolved ref:** Verify that an `interfaceRef` in `DriverInterface.spec.children` pointing at a missing CRD produces a `Degraded` condition on the parent DriverInterface and a `Degraded` condition on any ExporterClass that depends on it.
- **Check A — tree-shape match (positions filled):** Verify that an exporter whose runtime driver tree matches the composed expected tree — including composite parents with the correct direct children — passes Check A. Include a composite case (`dutlink → {power, serial, storage}`) and a deeper case (two levels of nesting).
- **Check A — slot-name match:** Verify that a runtime child whose `fileDescriptorProto.package` matches the expected interface BUT whose `labels["jumpstarter.dev/name"]` differs from the expected slot name does not satisfy that slot. Conversely, verify that a child with the right slot name but the wrong interface package does not satisfy it either. Both conditions must hold.
- **Check A — multiple children of the same interface, different slot names:** Construct a composite (e.g., a board with two `power-v1` children named `main` and `aux`) declared in the parent DriverInterface's `spec.children` with distinct `name`s. Verify each runtime child is matched to its own slot by the `jumpstarter.dev/name` label.
- **Check A — missing root position:** Verify that an exporter missing a root-level required interface fails Check A with reason `MissingInterface` and a message naming the accessor path (e.g., `device.dutlink`).
- **Check A — missing child position:** Verify that an exporter whose parent driver is present but missing a required child fails Check A with a message naming the full child accessor path (e.g., `device.dutlink.serial`).
- **Check A — missing parent transitively fails children:** Verify that when a required parent is missing, the condition lists the parent as the root cause and each of its required children with a `parent-missing` annotation rather than as separate independent missing-interface lines.
- **Check A — strict positional match:** Verify that a driver whose `interfacePackage` matches a declared interface but which sits at the **wrong relative position** (e.g., a `power` driver at root when the ExporterClass declares it as a child of `dutlink`) does *not* satisfy the declared position. The declared position remains "missing" and the misplaced driver is treated as an extra.
- **Check A — extras allowed:** Verify that extra drivers in the tree (e.g., a `monitoring` driver not declared by any ExporterClass entry) do not cause Check A to fail.
- **Check B — structural compatibility (descriptor matches):** Verify that a driver present in the tree whose `file_descriptor_proto` matches the DriverInterface's canonical `proto.descriptor` (same method names, same request/response message field types, same streaming flags) passes Check B.
- **Check B — structural compatibility (descriptor mismatches):** Verify that the controller catches each of: missing method, extra method ignored, parameter-type difference, return-type difference, streaming-flag mismatch (unary↔server_streaming, client_streaming↔bidi). Reason: `InterfaceStructuralMismatch`.
- **Both checks fail simultaneously:** Verify that when an exporter has one missing required interface AND another required interface present-but-mismatched, the condition's reason is `MultipleFailures` and the message names both the missing interface and the structural mismatch.
- **Optional interface handling:** Verify that missing optional interfaces, and optional interfaces present with descriptor mismatches, do not prevent an exporter from satisfying an ExporterClass — both surface as informational status only.
- **Pre-JEP-0011 driver in tree:** Verify that a driver with an empty `file_descriptor_proto` cannot be matched by Check A; if a required interface relies on such a driver, the exporter fails compliance with reason `MissingInterface`.
- **ExporterClass inheritance:** Verify that an `extends` chain correctly merges interface requirements from parent and child ExporterClasses.
- **Circular inheritance detection:** Verify that a circular `extends` chain is detected and results in a `Degraded` condition.
- **Label selector evaluation:** Verify that `matchLabels` and `matchExpressions` correctly filter exporters by their labels, and that all selector criteria must pass for a match.
- **Selector merging:** Verify that the controller correctly applies ExporterClass label selectors alongside the lease request's `selector`.
- **Declared-class compliance:** Set `Exporter.spec.exporterClassName` to a class the exporter satisfies and verify `ExporterClassCompliance: True` plus `satisfiedExporterClasses` containing the declared class followed by its ancestors via `extends`.
- **Declared-class mismatch:** Set `Exporter.spec.exporterClassName` to a class the exporter does *not* satisfy and verify `ExporterClassCompliance: False` with specific mismatch details; verify `satisfiedExporterClasses` does not contain that class.
- **Inferred membership:** Without setting `spec.exporterClassName`, verify the exporter is still tagged with the (non-`isBase`) ExporterClasses whose selectors match and whose interfaces it satisfies, and that an `ExporterClassMembership` informational condition lists them.
- **Lease subclass match (default):** Request a lease with `exporterClassName: embedded-linux-board`. Verify it binds to a `jetson-orin-nx` exporter whose declared class extends `embedded-linux-board`.
- **Lease exact match:** Request a lease with `exporterClassName: embedded-linux-board, exactMatch: true`. Verify it rejects a `jetson-orin-nx` exporter and only binds to exporters whose `spec.exporterClassName` is exactly `embedded-linux-board`.
- **Lease class + selector AND:** Request a lease with both `exporterClassName` and `selector`. Verify only exporters satisfying *both* are eligible; verify the rejection error names whichever dimension excluded the closest miss.

### Integration Tests

- **End-to-end lease with ExporterClass enforcement:** Apply DriverInterface and ExporterClass CRDs, register a compliant exporter and a non-compliant exporter with the same labels, request a lease with those labels, and verify the lease binds only to the compliant exporter.
- **End-to-end lease by class name:** Register exporters with `spec.exporterClassName` set, request a lease specifying `exporterClassName` only (no selector), and verify the lease binds to a matching exporter — including subclass matches by default and exact-match-only when `exactMatch: true`.
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

## Acceptance Criteria

Specific, testable conditions that must be met for the implementation of this JEP to be considered complete. These are the bar reviewers will hold the implementation PR(s) against.

- [ ] `DriverInterface`, `DriverClient`, `DriverImplementation`, and `ExporterClass` CRDs are installable via the operator's bundled-CRDs directory (`kubectl apply -f <bundle>` succeeds and the API server admits the resources).
- [ ] `Exporter.spec.exporterClassName` is accepted by the API server and persisted. Setting it produces an `ExporterClassCompliance` condition on `ExporterStatus` reflecting Check A + Check B results, with reason values drawn from the `MissingInterface` / `InterfaceStructuralMismatch` / `MultipleFailures` set.
- [ ] `Lease.spec.exporterClassName` and `Lease.spec.exactMatch` are accepted by the API server and honored by the lease controller's candidate filter — verified by the lease-time integration tests under *Test Plan* (subclass-match, exact-match, class + selector AND-combine).
- [ ] `make driver-registry` walks `interfaces/proto/**/driver.yaml`, validates each manifest against its sibling `.proto`, compiles the descriptor, and emits a complete `DriverInterface` CRD into the operator's bundled-CRDs directories (Helm and OLM). Re-running the target and `git diff --exit-code`-ing the bundled-CRDs directory produces no diff (CI passes).
- [ ] `jmp admin generate driverinterface ./driver.yaml` emits a complete `DriverInterface` CRD byte-identical to what the build target produces for the same input.
- [ ] `jmp admin {get,apply,generate} {driverinterface,driverclient,driverimpl,exporterclass}` and `jmp validate {exporter,client}` are implemented, documented, and pass their integration tests.
- [ ] Expected-tree composition (Step 0b of *Exporter Validation Algorithm*) correctly resolves a multi-level composite (a `dutlink-v1` declaring `power`/`serial`/`storage` children). Cycles in `DriverInterface.spec.children` produce a `Degraded` condition on the offending interface; unresolved `interfaceRef`s produce `Degraded` on the parent.
- [ ] Strict positional matching (Check A) rejects a misplaced driver — a `power-v1` driver at the runtime root does not satisfy a declared `dutlink → power` slot, and the matching `dutlink → power` slot remains "missing". Verified by the strict-positional-match unit test.
- [ ] Structural compatibility (Check B) catches the canonical drift cases (missing method, parameter-type mismatch, return-type mismatch, streaming-flag mismatch) and reports each with a per-position accessor path (e.g., `device.dutlink.power`).
- [ ] `ExporterStatus.Drivers []DriverInstance` is populated faithfully from the wire `DriverInstanceReport` tree at `Register` time. `InterfacePackage` is parsed from `FileDescriptorProto`; `InterfaceRef` is the matched DriverInterface CRD name. The deprecated `Devices []Device` field is populated in parallel for one release.
- [ ] At least one ExporterClass (`embedded-linux-board` or `embedded-linux`) and at least one composite `DriverInterface` (`dutlink-v1`) are defined in the project's CI fixtures and exercised by an end-to-end lease test.
- [ ] User-facing documentation: a Console / Admin Guide page explains the `driver.yaml` authoring workflow, the build target, and the relationship between source manifests and the cluster CRDs.
- [ ] Backward-compatibility regression suite passes: existing label-only lease requests behave exactly as before; existing exporter manifests without `spec.exporterClassName` register successfully and serve label-only leases.

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

- Existing label-only lease requests work unchanged. Clients that don't set `Lease.spec.exporterClassName` continue to get the original `selector`-driven behavior, with ExporterClass enforcement applied as a transparent backstop (the same way it works in JEP-0015's "label-only" flow).

- The `ExporterClass`, `DriverInterface`, `DriverClient`, and `DriverImplementation` CRDs are new resources. They don't modify existing CRDs in a breaking way. Clusters without these CRDs installed behave exactly as before.

- `Exporter.spec.exporterClassName` and `Lease.spec.exporterClassName` are **new optional fields**. Manifests authored before this JEP omit them and continue to work — exporters without a declared class fall through to label-only matching; leases without a declared class fall through to selector-only matching.

- Exporter registration is unchanged. The controller's additional ExporterClass evaluation is a read-only check that populates `status.satisfiedExporterClasses` and a couple of conditions. Exporters that predate ExporterClass are simply not tagged and not constrained.

- Exporters that don't embed `FileDescriptorProto` (deployments predating JEP-0011's descriptor-in-report path) are not affected by ExporterClass enforcement — they cannot be validated and are treated as non-compliant for ExporterClass purposes, but remain fully functional for label-only leases.

- The `jmp admin` subcommands (`get/apply/generate` for `driverinterface`/`driverclient`/`driverimpl`, `get/apply exporterclass`, `jmp validate exporter`, `jmp validate client`) are new. No existing CLI commands are modified.

- Driver-registry CRDs for bundled drivers are included in the operator installation. Upgrading Jumpstarter automatically updates these CRDs. Existing clusters without them continue to function exactly as before — the feature is fully opt-in.

- `ExporterStatus.Devices` is **deprecated** but retained and populated for one release alongside the new `ExporterStatus.Drivers` field. Out-of-tree consumers that grep for `.status.devices` keep working through the deprecation window. New code (controller, admin consoles, CLI) consumes `Drivers`. The legacy `Device` type keeps its existing JSON tags (including `parent_uuid` snake_case) unchanged; the new `DriverInstance` type uses `parentUuid` (camelCase) and the richer set of fields. The deprecated field is scheduled for removal in the release after first introducing `Drivers`.

## Consequences

### Positive

- **Typed device contracts.** Tests can declare the device shape they expect (`embedded-linux-board`, `jetson-orin-nx`) and the lease layer guarantees an exporter that conforms. Codegen for typed clients (a future codegen JEP) becomes feasible because the validator anchors the shape.
- **Single source of truth per interface.** Composition is declared once on the `DriverInterface` (via `driver.yaml`) and referenced by every consumer. No duplication across ExporterClasses; no convention to keep in sync.
- **Polyglot-friendly registry.** `DriverClient` and `DriverImplementation` CRDs are language-agnostic (`typeRef`, `language`, `packageUrl`). Adding Java, Go, or Rust support requires no schema changes — only new rows.
- **Fleet visibility.** `ExporterClass.status.satisfiedExporterCount` and `DriverInterface.status.{registeredClients, registeredImplementations, implementationCount}` give operators an at-a-glance view of capacity and registry depth without scripting.
- **Backward compatible.** All new fields are optional. Pre-JEP-0015 exporter and lease manifests continue to work via label-only selection, with ExporterClass enforcement applied as a transparent backstop.
- **Stable cluster identifiers.** Every cross-reference resolves to the same `metadata.name` from source `driver.yaml` to build output to cluster CRD, so renames are reachable by grep and there is no proto-package-to-CRD-name translation step to maintain.
- **Source-of-truth alignment with JEP-0011.** Compiled `FileDescriptorProto`s are pure build artifacts; the `.proto` is the only committed proto-shaped source. No large base64 blobs in source diffs and no regenerate-before-commit rule on authors.

### Negative

- **Four new CRDs to author and maintain.** Each bundled driver ships a source `driver.yaml`; the build produces three cluster CRDs per driver (`DriverInterface`, `DriverClient`, `DriverImplementation`). Out-of-tree drivers must ship the same set. The build target hides most of the work, but the surface area is real.
- **No direct `kubectl apply -f driver.yaml`.** The source manifest intentionally omits `spec.proto.descriptor`; users go through `jmp admin generate driverinterface` or the operator's bundled CRDs to get a cluster-applicable artifact. Trade-off accepted in DD-8.
- **`ExporterStatus.Devices` deprecation.** Out-of-tree consumers reading `.status.devices` keep working for one release; after that they must migrate to `.status.drivers`. A migration window is provided but the change is visible.
- **Tree-shape contract is rigid.** Strict positional matching (DD-13) means a driver that's "morally equivalent" but lives at the wrong tree position does not satisfy a declared slot. This is deliberate (codegen and predictability) but means contract authors must carefully shape their `DriverInterface.spec.children`.
- **More moving parts at registration time.** The controller now reconstructs the runtime tree, composes the expected tree by walking the registry, and runs two validation checks. Most of this is bounded by the size of one exporter's driver tree, but registration is more CPU-intensive than today.

### Risks

- **`driver.yaml` review burden.** Authors must keep manifest content (display fields, composition, name) in sync with `.proto` changes that affect them. Mitigation: build-time pairing checks (`spec.proto.package` must match the `.proto`'s `package` declaration; `metadata.name` must be unique across the tree).
- **CRD-name collisions across out-of-tree drivers.** Two third-party drivers might pick the same `metadata.name` (e.g., `my-driver-v1`). Mitigation: documented convention recommends a vendor prefix and the namespace boundary contains collisions within a tenant. Apply-time admission will fail noisily on a same-name collision in the same namespace.
- **Slot-label divergence.** Validation relies on the `jumpstarter.dev/name` label populated by `Driver.report()`. If a future change drops or renames the label, composite-slot validation breaks silently for affected drivers. Mitigation: pin the label as part of the JEP-0011 contract (already populated today) and assert its presence in Check A — drivers reporting children with no `jumpstarter.dev/name` are treated as unidentifiable for slot-matching and the operator sees a `MissingInterface` condition with a specific reason naming the missing label.
- **Build/runtime descriptor drift.** The cluster CRD's `spec.proto.descriptor` is generated at build time and pinned in the operator bundle; a driver package that upgrades its `.proto` without re-running the bundle build will see Check B failures. Mitigation: `make driver-registry` is wired into `make manifests`, and CI enforces `git diff --exit-code` after re-running it.
- **Validation cost at scale.** Large composite trees + many `DriverInterface`s with children could make Check A expensive per `Register`. Expected sizes (a few dozen drivers per exporter, low single-digit composite depth) make this a non-issue in practice, but worth keeping an eye on as the fleet grows. Mitigation: caching the composed expected tree per ExporterClass between exporter registrations; invalidating only on ExporterClass or DriverInterface changes.

## Rejected Alternatives

### Embedding interface requirements in labels

An early approach considered encoding interface requirements as exporter labels (e.g., `jumpstarter.dev/has-power=true`, `jumpstarter.dev/has-serial=true`) and matching them with standard label selectors. This was rejected because labels are unstructured strings with no validation — they can't express versioning, optional vs. required semantics, or structural compatibility. They also pollute the label space and require manual synchronization between exporter configuration and label values.

### Using annotations instead of CRDs

An alternative considered storing ExporterClass definitions as annotations on a shared ConfigMap. This was rejected because annotations have a 256 KB size limit, lack schema validation, don't support status subresources, and don't integrate with Kubernetes RBAC or the controller's informer/watch infrastructure.

### Defining ExporterClass as a gRPC-only API (no CRD)

An alternative considered defining ExporterClass as a gRPC service on the controller (rather than a Kubernetes CRD). This was rejected because CRDs provide declarative management via `kubectl apply`, RBAC integration, status subresources, and watch semantics for free — all of which a gRPC API would need to reimplement. ExporterClasses are cluster configuration, not runtime data; CRDs are the natural Kubernetes primitive for this.

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

- **gRPC Server Reflection** — Specified by JEP-0011 as part of the exporter's runtime surface. Drivers expose `FileDescriptorProto` at runtime via the gRPC reflection service, which is the source mechanism for comparison against the DriverInterface's canonical descriptor.

## Unresolved Questions

### Can wait until implementation

1. **Admission webhook:** Should the operator include a validating admission webhook that rejects malformed ExporterClasses (circular `extends`, missing `interfaceRef`) at apply time, or is controller-side validation with status conditions sufficient?

2. **Interface requirement weight/priority:** Should interface entries support a `priority` or `weight` field for lease scheduling? E.g., prefer exporters that satisfy more optional interfaces when multiple candidates match.

3. **ExporterClass discovery API:** Should `jmp admin get exporterclasses` query the cluster or work from local YAML files? Both have use cases — cluster for production, local for development.

## Future Possibilities

The following are **not** part of this JEP but are natural extensions enabled by it:

- **CEL-based selectors:** The current design uses standard Kubernetes label selectors. If more expressive power is needed (arbitrary boolean logic, string operations, access to structured device attributes), CEL expressions could be added as an alternative selector mechanism. The `cel-go` library is already available as an indirect dependency in the controller's module graph.

- **Polyglot typed device wrappers (future codegen JEP):** The ExporterClass definition provides everything needed to generate typed device classes in any language — `EmbeddedLinuxBoardDevice` with `power: PowerClient`, `serial: SerialClient`, `storage: StorageClient` as non-nullable fields and `network: NetworkClient?` as nullable.

- **Driver registry integration (future Registry JEP):** A registry can catalog which driver packages implement which DriverInterfaces, and which ExporterClasses they satisfy, enabling `jmp registry list exporter-classes` and `jmp registry describe exporter-class embedded-linux-board`. The registry could also serve as an alternative source for `proto.descriptor` resolution, supplementing the inline embedding approach.

- **Capacity planning dashboard:** With `status.satisfiedExporterCount` on every ExporterClass, a web dashboard could show real-time fleet capacity per device profile, utilization rates, and availability trends.

- **ExporterClass-aware scheduling:** The controller's lease scheduler could use ExporterClass satisfaction metadata for smarter scheduling — preferring exporters that satisfy the most optional interfaces, or load-balancing across ExporterClasses with the most available capacity.

- **Test matrix generation:** ExporterClass definitions could drive test matrix generation — automatically running a test suite against every ExporterClass that the test's required interfaces are a subset of.

- **Interface-spec maturity.** A `maturity` enum on `DriverInterface` (e.g., `Experimental | Beta | Stable | Deprecated`) would let admin consoles flag in-development interfaces distinct from operational `Ready` conditions. Deferred until a concrete consumer needs it.

- **DriverImplementation drift detection.** The controller could surface a `Warning` condition on exporters whose reported driver `typeRef`s don't match any registered `DriverImplementation`. Useful for catching out-of-band driver installs and driver-name drift. Strictly a console-visible signal — never fails the lease.

## Implementation Phases

| Phase | Deliverable                                                                                                                                                                              | Depends On        |
| ----- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------- |
| 1     | `DriverInterface` + `DriverClient` + `DriverImplementation` CRD definitions, `make driver-registry` build target, bundled YAMLs in Helm/OLM                                              | JEP-0011          |
| 2     | `ExporterClass` CRD definition + controller validation on registration + lease enforcement                                                                                               | Phase 1           |
| 3     | `jmp admin` CLI tooling (`get/apply/generate` for `driverinterface`/`driverclient`/`driverimpl`, `get/apply exporterclass`) + `jmp validate exporter` + `jmp validate client`            | Phase 1           |
| 4     | Structural validation via `FileDescriptorProto` comparison (moderate strictness)                                                                                                         | Phase 2, JEP-0011 |
| 5     | ExporterClass inheritance (`extends`)                                                                                                                                                    | Phase 2           |
| 6     | Optional: controller cross-check that an exporter's reported driver `typeRef`s match registered `DriverImplementation` CRDs; surface mismatches as `Warning` conditions (non-fatal)      | Phase 2           |

Phases 1–2 are the minimum viable deliverable: named device contracts with controller-enforced lease matching, requiring no client-side changes. Phase 3 provides admin tooling. Phases 4–5 add the structural depth enabled by JEP-0011's proto introspection. Phase 6 is an optional console-visible drift detector.

## Implementation History

- 2026-04-06: JEP drafted as "DeviceClass Mechanism".
- 2026-04-08: Renamed to "ExporterClass Mechanism" (`DeviceClass` → `ExporterClass`, `InterfaceClass` → `DriverInterface`). Replaced CEL selectors with standard Kubernetes label selectors. Changed CRD scope from cluster-scoped to namespace-scoped. Added schema distribution and CLI workflow section. Added `descriptor` for inline canonical `FileDescriptorProto` storage. Added schema registry prior art (Buf BSR, Confluent, Envoy). Resolved all "must resolve before acceptance" design questions. Moved DriverInterface distribution to Jumpstarter installation (Helm/OLM bundling) instead of manual publishing. Moved admin CLI commands under `jmp admin`. Removed `config` section from ExporterClass (driver configuration belongs in ExporterConfig, not in the typing contract).
- 2026-05-10: Renumbered from JEP-0012 to JEP-0015 and rebased onto the accepted JEP-0011 (proto-first, build-time descriptor compilation). Removed the JEP-0011 PoC Readiness and Gaps sections — the prior PoC's surface (`@driverinterface` decorator, `build_file_descriptor()` runtime call, `jmp proto export`) no longer matches JEP-0011, and no PoC for the ExporterClass / DriverInterface CRD path currently exists. Rewrote descriptor-flow language to describe `.proto` → `protoc --descriptor_set_out` → DriverInterface `proto.descriptor`, with runtime exposure via gRPC reflection and `DriverInstanceReport.file_descriptor_proto`. Generalized forward references to typed codegen as "a future codegen JEP" and to the driver registry as "a future Registry JEP".
- 2026-05-10: Split the implementation registry into separate CRDs to support the OpenShift Console prototype. `DriverInterface` now carries only the proto contract plus `displayName`/`description`/status counters. New `DriverClient` CRD registers a per-`(interface, language)` client implementation (`typeRef`, `package`, `version`, `index`, `repoUrl`, `packageUrl`, `note`); `typeRef` is the language-specific FQN / import path so the schema is language-agnostic across Python classes, Java classes, Go types, Rust types, etc., and `packageUrl` is the language-agnostic direct link to the package page on its registry (PyPI, Maven Central, crates.io, npm, …). New `DriverImplementation` CRD (short alias `driverimpl`) registers each concrete driver-side type (`typeRef`, `language`, `package`, `version`, `description`, `sourceUrl`). `ExporterClass` gained `displayName`, `description`, `vendor`, `arch`, `isBase` for console rendering, and `status.resolvedInterfaces` entries now carry `inheritedFrom`. Made explicit that method-level metadata is read from the `FileDescriptorProto`'s `source_code_info` — not duplicated in the CRD. Deferred broader discovery / browse RPCs to JEP-0014 (Admin API) and an interface-spec `maturity` enum to a future iteration.
- 2026-05-10: Specified the lease-time interaction between ExporterClass, labels, and `extends`. Added `Exporter.spec.exporterClassName` (declared primary class, K8s `*ClassName` convention) and `Lease.spec.exporterClassName` + `Lease.spec.exactMatch`. Lease filtering AND-combines `selector` and `exporterClassName`. By default, requesting a class also matches exporters whose declared class extends it (subclass match); `exactMatch: true` requires an exact declared-class equality. `status.satisfiedExporterClasses` now lists the declared class first, then its ancestors via `extends`, then any inferred memberships. Split the `ExporterClassCompliance` condition (declared-class validation) from the new `ExporterClassMembership` condition (inferred memberships). All new fields are optional — pre-JEP-0015 manifests keep working via label-only lease matching.
- 2026-05-10: Tightened the exporter validation algorithm to be **two explicit, independent checks** that must both pass: Check A (tree completeness — every required `DriverInterface` has a matching driver somewhere in the `DriverInstanceReport` tree, identified by `FileDescriptorProto.package`) and Check B (structural compatibility — each found driver's `file_descriptor_proto` matches the canonical `DriverInterface.proto.descriptor` for method names, request/response message field types, and streaming semantics). Added distinct `ExporterClassCompliance` condition reasons (`MissingInterface`, `InterfaceStructuralMismatch`, `MultipleFailures`) so operators can tell "missing driver" from "wrong driver version". Expanded the Test Plan to cover each failure mode independently.
- 2026-05-10: Made the ExporterClass declare the **tree shape** of the expected driver hierarchy, not just a flat set. `ExporterClass.spec.interfaces[*]` gained an optional recursive `children` array; `status.resolvedInterfaces` mirrors the same tree shape; `extends` merges trees by `name` at each level. Check A became a strict **positional** tree-shape match (root entries match root drivers; `children` entries must be direct children of their matched parent), with extras allowed and missing parents transitively flagging missing children. Replaced the dead `ExporterStatus.Devices []Device` projection with `ExporterStatus.Drivers []DriverInstance` — a faithful CRD-side mirror of the wire `DriverInstanceReport` (uuid, parentUuid, labels, description, methodsDescription, fileDescriptorProto) plus controller-computed `interfacePackage` (parsed from the descriptor) and `interfaceRef` (matched DriverInterface CRD name). Renamed the parent-ref JSON tag to `parentUuid` (camelCase) on the new type while leaving the deprecated `Device.parent_uuid` (snake_case) tag untouched. `Devices` is deprecated and retained for one release before removal. Added the `DriverTreeInvalid` condition for reconstruction failures (orphaned parent refs, cycles).
- 2026-05-10: Moved composition (which child interfaces a composite must expose) from `ExporterClass.spec.interfaces[*].children` onto `DriverInterface.spec.children`, where it belongs as part of the interface contract. `ExporterClass.spec.interfaces` is now a flat list of root-level requirements; the controller composes the expected tree at validation time by walking each ExporterClass entry's `interfaceRef` and recursively expanding `DriverInterface.spec.children`. Runtime children are matched to declared slots by the existing `jumpstarter.dev/name` label populated by `Driver.report()` plus a `fileDescriptorProto.package` match — both must hold for a slot to be considered filled. Inheritance via `extends` reverted to a flat root-level merge by `name`.
- 2026-05-10: Specified the **DriverInterface manifest** — a YAML file committed alongside each `.proto` under `interfaces/proto/<package>/<interface>.yaml` (same stem) that carries every non-proto interface metadata field: `package`, `displayName`, `description`, and `children`. `interfaceRef` in `children[*]` uses the **proto package** of the referenced interface (universal identifier; the build target maps to CRD name at emit time). This closes the JEP-0011 gap that proto can't express: gRPC services cannot be nested, so composition has to live in a parallel artifact. `make driver-registry` (and `jmp admin generate driverinterface`) read the proto + manifest pair and emit the DriverInterface CRD; mismatched or missing pairs are build errors.
- 2026-05-10: Refined the DriverInterface manifest: renamed the file from `<interface>.yaml` to the fixed name `driver.yaml` (every interface directory ships one, including leaves — uniform tooling). Added a required `name` field carrying the cluster-side DriverInterface CRD name. Cross-references (`children[*].interfaceRef`, ExporterClass `interfaceRef`, DriverClient `interfaceRef`, DriverImplementation `interfaceRef`) now resolve against this `name` rather than the proto package — the same identifier is used everywhere (source manifest, build output, cluster CRD), so no proto-package-to-CRD-name translation is needed and cross-file references are explicit and stable. The manifest's `package` field is retained for build-time pairing validation against the sibling `.proto`'s `package` declaration.
- 2026-05-10: Standardized the default `name` convention to a **straight dots→hyphens conversion of the proto package** — `jumpstarter.driver.dutlink.v1` becomes `jumpstarter-driver-dutlink-v1`. Replaces the prior `dev-<org>-<interface>-<version>` placeholder convention. Keeps the cluster identifier visually parallel to the proto package and unambiguous; authors retain the freedom to override with any DNS-safe slug for out-of-tree drivers. Updated every YAML example, prose reference, and naming description in the JEP to use the new convention.
- 2026-05-10: Versioned the `driver.yaml` manifest the same way as every other Jumpstarter config and CRD YAML — `apiVersion: jumpstarter.dev/v1alpha1` and `kind: DriverInterface`, with fields organized under `metadata` and `spec`. Moved `name` to `metadata.name`, moved `package` to `spec.proto.package`, and moved `displayName`/`description`/`children` under `spec`. Adopting the standard K8s shape keeps schema evolution and tooling (kubectl, kustomize, pydantic config loaders) uniform across all Jumpstarter YAML kinds.
- 2026-05-10: Reverted "descriptor committed in `driver.yaml`" to align with JEP-0011's principle that compiled `FileDescriptorProto`s are pure build artifacts, not committed alongside the `.proto`. Source `driver.yaml` is now a **partial `DriverInterface` CRD** that intentionally omits `spec.proto.descriptor`; the build target reads source manifest + sibling `.proto`, compiles the descriptor, and emits a complete CRD into the operator's bundled-CRDs directory (Helm chart / OLM bundle / generated CRDs dir). Source is never rewritten in place. The applied artifact (what the operator installs, what `jmp admin generate driverinterface` prints) is the generated CRD; the committed source is the authoring form. Trades off "`kubectl apply -f driver.yaml` works directly" against not having large base64 binary blobs in source diffs and not requiring authors to regenerate-before-commit — JEP-0011's source-of-truth model wins.
- 2026-05-10: Aligned the JEP with the template's mandatory-section structure for Standards Track. Promoted the inline four-item "Resolved Design Decisions" subsection to a top-level **Design Decisions** section with 16 explicit `DD-N` entries (alternatives / decision / rationale) covering every significant choice made during the JEP's development. Added a top-level **Acceptance Criteria** section with a checklist of specific, testable conditions for the implementation to be considered done (distinct from Graduation Criteria, which governs the experimental→stable transition). Added a top-level **Consequences** section with Positive / Negative / Risks subsections summarizing the expected outcomes, including the trade-offs accepted in DDs (no direct `kubectl apply`, four CRDs per driver, etc.) and the risks worth tracking (CRD-name collisions, slot-label divergence, validation cost at scale).

## References

- [JEP-0011: Protobuf Introspection and Interface Generation](./JEP-0011-protobuf-introspection-interface-generation.md)
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
