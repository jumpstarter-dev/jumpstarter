# JEP-0014: Virtual Scalable Exporters

| Field             | Value                                                          |
| ----------------- | -------------------------------------------------------------- |
| **JEP**           | 0014                                                           |
| **Title**         | Virtual Scalable Exporters                                     |
| **Author(s)**     | @mangelajo (Miguel Angel Ajo Pelayo)                           |
| **Status**        | Draft                                                          |
| **Type**          | Standards Track                                                |
| **Created**       | 2026-06-03                                                     |
| **Updated**       | 2026-06-03                                                     |
| **Discussion**    | https://github.com/jumpstarter-dev/jumpstarter/issues/41       |
| **Requires**      |                                                                |
| **Supersedes**    |                                                                |
| **Superseded-By** |                                                                |

---

## Abstract

This JEP proposes a Virtual Scalable Exporter subsystem for Jumpstarter that
manages pools of virtual targets with configurable autoscaling. Each virtual
target definition declares a minimum and maximum number of instances; the system
maintains a warm pool of pre-spawned exporters ready for immediate lease
fulfillment, and scales up or down based on demand. This enables low-latency
lease acquisition, massive scalability, resource efficiency, and simplified
orchestration of mixed physical/virtual test topologies — while allowing
administrators to tune the trade-off between responsiveness and resource
consumption on a per-target basis.

## Motivation

Jumpstarter currently excels at managing scarce, physical hardware targets.
However, testing and development often require a mix of physical devices and
scalable, virtual resources. Today, virtual targets must be manually deployed
as static exporters with a fixed count — there is no mechanism for the system
to maintain or scale a pool of virtual instances based on demand.

This model has several limitations:

- **Artificial scarcity:** Virtual targets are treated as a fixed-size pool,
  just like physical ones, which defeats their "virtually unlimited" potential.
- **No elasticity:** The pool cannot grow when demand spikes (CI burst) or
  shrink when idle, leading to either queuing or waste.
- **Manual lifecycle:** Administrators must manually deploy, monitor, and scale
  virtual exporter instances — there is no declarative "desired state" for a
  virtual target pool.
- **Cold-start penalty vs. waste trade-off:** Users must choose between
  pre-spawning many instances (wasting resources when idle) or spawning on
  demand (high latency at lease time). There is no middle ground.

The core problem is that virtual targets lack a pool manager that can maintain a
configurable warm pool while autoscaling to meet demand.

### User Stories

- **As a** CI pipeline author, **I want to** lease N virtual targets instantly
  from a warm pool, **so that** my pipeline doesn't block on provisioning
  latency during burst periods.

- **As a** developer, **I want to** lease a virtual target matching a known
  physical board's properties with near-zero wait time, **so that** I can
  iterate quickly without waiting for scarce hardware.

- **As a** platform engineer, **I want to** declare a virtual target pool with
  `minInstances: 2, maxInstances: 20`, **so that** there are always warm
  instances ready while the system scales up on demand and scales down when idle.

- **As a** cost-conscious operator, **I want to** set `minInstances: 0` for
  rarely-used target types, **so that** they consume no resources until actually
  requested, accepting a cold-start delay.

## Proposal

The proposal introduces **Virtual Scalable Exporters** — a controller-managed
pool of virtual target instances with configurable autoscaling. Rather than
treating virtual targets as purely on-demand or purely static, each virtual
target definition declares scaling parameters that let administrators tune the
trade-off between instant availability and resource consumption.

### Core Concept: Managed Pools with Scaling

Each provider type has its own CRD (e.g., `QEMUExporterPool`,
`AndroidExporterPool`, `CorelliumExporterPool`) with provider-specific
configuration fields alongside shared scaling parameters. This gives each
provider a strongly-typed schema rather than a generic bag of config.

**Example: QEMU pool**

```yaml
apiVersion: jumpstarter.dev/v1alpha1
kind: QEMUExporterPool
metadata:
  name: rpi4-virtual
  namespace: jumpstarter
spec:
  # Scaling configuration (shared across all pool CRDs)
  minInstances: 2        # Always keep 2 warm instances ready
  maxInstances: 20       # Scale up to 20 under load
  
  # Node scheduling (shared across all pool CRDs, optional)
  nodeSelector:
    node.kubernetes.io/instance-type: bare-metal
    jumpstarter.dev/nested-virt: "true"
  
  # Labels exposed on each instance (for lease matching)
  labels:
    board: rpi4
    arch: aarch64
    virtual: "true"
  
  # Pod overrides (shared across all pool CRDs, optional)
  podTemplate:
    resources:
      requests:
        cpu: "4"
        memory: 5Gi
      limits:
        cpu: "4"
        memory: 5Gi
  
  # QEMU-specific configuration
  machineType: virt
  firmware: registry.example.com/firmware/rpi4:latest
  resources:
    cpu: 4
    memory: 4Gi
    storage: 16Gi
    
  # Exporter template (drivers exposed by each instance)
  exporterTemplate:
    drivers:
      - type: jumpstarter_driver_power.driver.QemuPower
      - type: jumpstarter_driver_network.driver.TcpNetwork
        config:
          port: 22
      - type: jumpstarter_driver_serial.driver.QemuSerial
```

**Example: Android Emulator pool**

```yaml
apiVersion: jumpstarter.dev/v1alpha1
kind: AndroidExporterPool
metadata:
  name: pixel7-emulator
  namespace: jumpstarter
spec:
  minInstances: 0        # Fully on-demand (cold-start OK for this target)
  maxInstances: 10
  
  labels:
    device: pixel7
    os: android
    api-level: "34"
    virtual: "true"
  
  # Android-specific configuration
  systemImage: system-images;android-34;google_apis;arm64-v8a
  avdProfile: pixel_7
  gpu: swiftshader
  
  exporterTemplate:
    drivers:
      - type: jumpstarter_driver_android.driver.AdbDriver
      - type: jumpstarter_driver_power.driver.EmulatorPower
```

**Example: Corellium pool**

The Corellium driver (`jumpstarter_driver_corellium.driver.Corellium`) manages
the full virtual instance lifecycle through the Corellium REST API — it creates
instances on power-on and destroys them on power-off. It exposes a power
interface and a websocket-based serial console. The pool controller manages the
exporter Pod and injects API credentials via environment variables
(`CORELLIUM_API_HOST`, `CORELLIUM_API_TOKEN`) from the referenced Secret.

```yaml
apiVersion: jumpstarter.dev/v1alpha1
kind: CorelliumExporterPool
metadata:
  name: rd1ae-corellium
  namespace: jumpstarter
spec:
  minInstances: 1
  maxInstances: 5
  
  labels:
    board: rd1ae
    flavor: kronos
    virtual: "true"
  
  # Corellium-specific configuration
  apiHost: app.corellium.com
  apiCredentialsSecret: corellium-api-credentials  # Secret with keys: token
  projectId: "778f00af-5e9b-40e6-8e7f-c4f14b632e9c"
  
  # Device/instance parameters
  deviceFlavor: kronos
  deviceOs: "1.1.1"
  deviceBuild: "Critical Application Monitor (Baremetal)"
  consoleName: "Primary Compute Non-Secure"
  
  exporterTemplate:
    drivers:
      - type: jumpstarter_driver_corellium.driver.Corellium
        config:
          device_name: "{{ .InstanceName }}"
```

The pool controller automatically injects the Corellium-specific CRD spec fields
(`projectId`, `deviceFlavor`, `deviceOs`, `deviceBuild`, `consoleName`) into the
driver config at instance creation time. Only fields that vary per instance (like
`device_name` using the `{{ .InstanceName }}` template variable) need to be
specified explicitly in `exporterTemplate`.

A pool with `minInstances: 0` consumes no resources until a lease is
requested, accepting cold-start latency. A pool with `minInstances: 3`
always has 3 ready-to-lease instances — leases are fulfilled instantly from
the warm pool, and the controller scales up if more are needed.

### User Experience

From the user's perspective, virtual scalable exporters appear as regular
exporters in the pool. The lease experience is unchanged:

```bash
# Lease any rpi4 target — may match physical or virtual
jmp lease -l board=rpi4

# Lease explicitly virtual targets
jmp lease -l board=rpi4,virtual=true

```

The guiding principle is: **"Get me a target that matches my requirements."** The
distinction between physical and virtual is an implementation detail, not a
primary concern for the user. Virtual exporters simply appear in the same pool
as physical ones, differentiated only by labels.

### Architecture Overview

```
                        ┌─────────────────────────┐
                        │  jumpstarter-controller │
                        │  (creates Leases,       │
                        │   assigns Exporters)    │
                        └──────────┬──────────────┘
                                   │
                      creates/updates Lease & Exporter objects
                                   │
                                   ▼
                  ┌────────────────────────────────────┐
                  │          Kubernetes API            │
                  │  (Lease CRs, Exporter CRs,         │
                  │   *ExporterPool CRs)               │
                  └─┬──────────────┬──────────────┬────┘
                    │              │              │
         watches    │   watches    │   watches    │
      Leases +      │  Leases +    │  Leases +    │
      Exporters     │  Exporters   │  Exporters   │
                    │              │              │
  ┌─────────────────▼┐ ┌───────────▼──────────┐┌──▼──────────────────────┐
  │ QEMUExporterPool │ │ AndroidExporterPool │ │ CorelliumExporterPool   │
  │ Controller       │ │ Controller          │ │ Controller              │
  └────────┬─────────┘ └──────────┬──────────┘ └────────────┬────────────┘
           │                      │                         │
           │ manages              │ manages                 │ manages
           ▼                      ▼                         ▼
  ┌──────────────────┐ ┌───────────────────────┐ ┌────────────────────────┐
  │ Warm Pool        │ │ Warm Pool             │ │ Warm Pool              │
  │ [inst1][inst2].. │ │ [inst1][inst2]..      │ │ [inst1]..              │
  └────────┬─────────┘ └───────────┬───────────┘ └────────────┬───────────┘
           │                       │                          │
           └───────────────────────┼──────────────────────────┘
                                   │ register as standard Exporter CRs
                                   ▼
                          Kubernetes API (Exporters)
```

**Scaling Inputs — Watches on Leases and Exporters:**

Each pool controller watches two key resources to make scaling decisions:

1. **Leases** — The controller watches for pending Leases whose label selectors
   match the pool's labels. Pending leases with no available exporter signal
   demand and trigger scale-up.
2. **Exporters** — The controller watches the Exporter objects it owns to track
   which instances are available (no active lease) vs. occupied (leased). This
   determines the current pool utilization.

Together these inputs feed the scaling logic: if there are pending leases that
match this pool and no available instances to serve them, scale up. If there are
excess idle instances beyond `minInstances` for a sustained period, scale down.

**Per-Provider Deployments (single image by default):** All provider
controllers are compiled into a single binary. Each Deployment in the cluster
passes a `--provider=<type>` flag to activate the corresponding reconciler.
This gives each provider isolated logs and independent restarts while
maintaining a single image to build and release. The per-provider `image`
override in the operator CR allows administrators to substitute a custom image
for a specific provider (e.g., a third-party provider distributed as its own
image) without affecting other providers.

The Jumpstarter operator deploys pool controllers based on the `Jumpstarter`
CR configuration. A new `exporterPools` section lists which providers to
enable, following the same pattern as `controller` and `routers`:

```yaml
apiVersion: operator.jumpstarter.dev/v1alpha1
kind: Jumpstarter
metadata:
  name: jumpstarter
  namespace: jumpstarter
spec:
  # ... existing controller, routers, authentication config ...
  
  # Pool controllers configuration (new)
  exporterPools:
    # Default image shared by all pool controllers (can be overridden per provider)
    image: quay.io/jumpstarter-dev/pool-controller:latest
    imagePullPolicy: IfNotPresent
    
    # List of providers to deploy controllers for
    providers:
      - name: qemu
        enabled: true
        resources:
          requests:
            cpu: 100m
            memory: 256Mi
      - name: android
        enabled: true
        resources:
          requests:
            cpu: 100m
            memory: 256Mi
      - name: corellium
        enabled: false
        # Override the default image for this provider
        image: quay.io/jumpstarter-dev/pool-controller-corellium:latest
        imagePullPolicy: Always
```

The operator creates one Deployment per enabled provider, passing
`--provider=<name>` to the shared binary. This gives administrators a single
knob to enable/disable pool controllers, and the operator handles RBAC,
service accounts, and Deployment lifecycle.

**Scaling Logic:** Each pool controller monitors its instances and scales based
on available (unleased) instances:

- If available instances drop below a threshold (e.g., `minInstances`), scale up.
- If available instances exceed demand for a cooldown period, scale down (never
  below `minInstances`).
- Never exceed `maxInstances`.

**Instance Lifecycle:**

1. Pool controller creates a Pod from the pool spec using provider-specific
   templates.
2. The Pod starts the virtual target (e.g., QEMU VM, Android emulator, or
   Corellium API call) and runs the Jumpstarter exporter, registering with
   the controller like any other exporter.
3. The instance becomes available in the pool for lease assignment.
4. When a lease is released, the exporter internally handles cleanup/reset
   (this is existing exporter behavior). The instance returns to the available
   pool automatically.


### API / Protocol Changes

**New CRDs: `*ExporterPool` (one per provider type)**

Each provider type defines its own CRD. All share a common scaling spec
(embedded struct in Go) but have provider-specific configuration fields:

```yaml
# Common fields shared by all *ExporterPool CRDs:
spec:
  # Scaling (common)
  minInstances: <int>            # Minimum warm pool size (default: 0)
  maxInstances: <int>            # Maximum pool size (required)
  scaleUpThreshold: <int>        # Scale up when available < this (default: minInstances)
  scaleDownCooldown: <duration>  # Wait before scaling down (default: 5m)
  
  # Node scheduling (common, optional)
  # Applied to instance Pods — use to target baremetal nodes, nodes with
  # nested virtualization, GPU nodes, specific architectures, etc.
  nodeSelector:
    <key>: <value>
  
  # Pod overrides (common, optional)
  # Customize the exporter Pod container image and resource requests/limits.
  # Providers set sensible defaults; these fields allow administrators to
  # override them per pool.
  podTemplate:
    image: <string>              # Override the default exporter container image
    resources:
      requests:
        cpu: <quantity>
        memory: <quantity>
      limits:
        cpu: <quantity>
        memory: <quantity>
  
  # Labels applied to all instances (common)
  labels:
    <key>: <value>
  
  # Exporter driver configuration template (common)
  exporterTemplate:
    drivers:
      - type: <driver-class>
        config: { ... }

# Provider-specific fields differ per CRD:
# - QEMUExporterPool: machineType, firmware, resources (cpu/mem/storage), ...
# - AndroidExporterPool: systemImage, avdProfile, gpu, ...
# - CorelliumExporterPool: apiHost, apiCredentialsSecret, projectId, ...
#   (CorelliumExporterPool typically does not use nodeSelector/podTemplate
#    since it provisions instances via external API, so local pods connect to the
#    corellium api, and the architecture/characteristics of the running node do not
#    matter.)
```

**Status subresource (common to all pool CRDs):**

```yaml
status:
  totalInstances: 5
  readyInstances: 3
  leasedInstances: 2
  conditions:
    - type: PoolHealthy
      status: "True"
    - type: ScalingLimited
      status: "False"
```

**Changes to existing CRDs:**

**Exporter — new `enabled` field:**

Exporters gain an `enabled` boolean field (default: `true`). When set to
`false`, the `jumpstarter-controller` will not assign new leases to this
exporter. This is useful for:

- **Lab operations:** Temporarily taking a physical exporter offline for
  maintenance without deleting it.
- **Graceful scale-down:** Pool controllers set `enabled: false` before
  terminating an instance, ensuring the controller doesn't race to assign a
  lease to an exporter that is about to be deleted.

```yaml
apiVersion: jumpstarter.dev/v1alpha1
kind: Exporter
metadata:
  name: qemu-rpi4-instance-3
spec:
  enabled: false  # Controller will not assign new leases to this exporter
```

The graceful scale-down sequence becomes:

1. Pool controller sets `enabled: false` on the target exporter.
2. Pool controller waits to confirm no lease was assigned (watches for
   `status.leaseRef` to remain empty).
3. Pool controller deletes the Pod and Exporter CR.

### Hardware Considerations

This proposal is specifically designed to reduce reliance on physical hardware
for scalable testing. However:

- Virtual targets must faithfully emulate the interfaces exposed by physical
  hardware (serial, network, storage, power) through the existing driver model.
- Providers like QEMU/Renode require `/dev/kvm` access for acceptable
  performance on the host nodes.
- Timing-sensitive tests (USB/IP latency, boot ROM timeouts) may behave
  differently on virtual targets — the system should expose labels indicating
  whether a target is physical or virtual so users can filter when fidelity
  matters.

## Design Decisions

### DD-1: Pool-based scaling vs. purely on-demand provisioning

**Alternatives considered:**

1. **Pool-based with configurable min/max** — Maintain a warm pool of
   pre-spawned instances; scale between `minInstances` and `maxInstances`.
2. **Purely on-demand** — Spawn a new instance only when a lease request arrives;
   destroy it when the lease is released.

**Decision:** Pool-based with configurable min/max.

**Rationale:** Purely on-demand provisioning introduces unacceptable latency for
CI pipelines (VM boot + exporter registration can take 30-120s). A warm pool
provides instant lease fulfillment for the common case. Setting `minInstances: 0`
still allows purely on-demand behavior for rarely-used targets, giving operators
full control over the trade-off.

### DD-2: Pool controller deployment model

**Alternatives considered:**

1. **Separate binary per provider** — Each provider is a completely independent
   binary/image (e.g., `jumpstarter-qemu-pool-controller`).
2. **Single binary, one deployment per provider** — One image contains all
   provider reconcilers; a CLI flag (`--provider=qemu`) selects which one to
   activate. Each provider gets its own Deployment in the cluster.
3. **Single binary, single deployment** — One Deployment runs all provider
   reconcilers together.
4. **Integrated into jumpstarter-controller** — Add pool reconcilers directly
   into the existing operator.

**Decision:** Option 2 — single binary, one Deployment per provider.

**Rationale:** A single image is cheaper to build, test, and productize — there
is one CI pipeline, one vulnerability scan, one release artifact. Deploying it
as separate Deployments (one per provider) gives operational benefits: each
provider has isolated logs, independent scaling, and can be restarted without
affecting other providers. The `--provider` flag makes it explicit which CRD
a given Deployment reconciles. Adding a new provider type means adding a new
Deployment manifest pointing to the same image with a different flag — no new
image build required.

### DD-3: CRD per provider vs. generic CRD

**Alternatives considered:**

1. **CRD per provider** (`QEMUExporterPool`, `AndroidExporterPool`, etc.) —
   Strongly typed, schema-validated, provider-specific fields at the top level.
2. **Single generic CRD** (`VirtualExporterPool`) with a `provider.type` field
   and opaque `provider.config` map.
3. **Generic CRD + ConfigMap reference** — Pool CRD references a ConfigMap
   containing provider-specific configuration.

**Decision:** CRD per provider.

**Rationale:** Strongly-typed CRDs give better UX (IDE completion, webhook
validation, clear documentation per provider). Each provider has fundamentally
different configuration (QEMU needs machine types and firmware images; Corellium
needs API credentials and device models) — a generic map loses type safety and
discoverability. New providers add a new CRD without touching existing ones.

### DD-4: Per-lease parameters vs. pool flavors

**Alternatives considered:**

1. **Per-lease `parameters` dictionary** — Leases carry an opaque
   `map[string]string` that pool controllers interpret when provisioning
   instances (e.g., override CPU, memory, or storage). The controller passes
   parameters through without interpretation; only pool controllers read them.
2. **Multiple pool flavors** — Administrators create separate pool CRs for
   different resource profiles (e.g., `rpi4-virtual-small` with 2 CPU / 2 Gi
   and `rpi4-virtual-large` with 8 CPU / 16 Gi). Users select a profile via
   label matching at lease time.

**Decision:** Option 2 — multiple pool flavors via separate pool CRs.

**Rationale:** Per-lease parameters add complexity across every layer: the Lease
CRD gains a new field, the controller must pass it through, pool controllers
must parse and validate provider-specific keys, driver templates must support
runtime overrides, and the interaction between parameters and pool defaults
(override vs. merge) must be defined and tested. All of this for a use case
that is already satisfied by creating multiple pools with different resource
profiles and letting users select via labels. The pool-flavors approach keeps
the Lease API unchanged, requires no controller modifications, and is
immediately understandable. Per-lease parameters can be revisited in a future
JEP if the pool-flavors model proves insufficient.

## Design Details

### Reconciliation Loop

Each pool controller runs a continuous reconciliation loop for its CRD,
triggered by changes to the pool CR, owned Exporters, or matching Leases:

```
for each *ExporterPool CR:
  ownedExporters = list Exporters owned by this CR
  currentInstances = count ownedExporters in Ready state
  leasedInstances = count ownedExporters with an active LeaseRef
  availableInstances = currentInstances - leasedInstances
  pendingLeases = count pending Leases whose labels match this pool's labels
  
  # Invariant: always maintain minInstances
  if currentInstances < spec.minInstances:
    scale up to spec.minInstances

  # Demand-driven scale-up: pending leases that we could serve
  elif pendingLeases > 0 AND currentInstances < spec.maxInstances:
    scale up by min(pendingLeases, spec.maxInstances - currentInstances)

  # Threshold-based scale-up: available pool running low
  elif availableInstances < spec.scaleUpThreshold AND currentInstances < spec.maxInstances:
    scale up (add instances to restore available pool)

  # Scale-down: excess idle instances beyond what we need
  elif availableInstances > spec.scaleUpThreshold AND cooldown elapsed:
    graceful scale down:
      1. set exporter.spec.enabled = false
      2. wait until confirmed no lease was assigned (leaseRef remains empty)
      3. delete Pod and Exporter CR
    (never below minInstances)
```

### Instance States

Each virtual exporter instance transitions through:

```
Provisioning → Ready (warm pool) → Leased → Ready
                                              └→ Terminating → (deleted if available instances>min)
```

- **Provisioning:** Pod is starting, VM booting, exporter registering.
- **Ready:** Instance is registered and available for lease.
- **Leased:** Instance is assigned to an active lease.
- **Terminating:** Instance being deleted (scale-down).

### Component Interaction

1. Administrator creates a `*ExporterPool` CR (e.g., `QEMUExporterPool`).
2. The corresponding pool controller provisions `minInstances` Pods.
3. Each Pod boots the virtual target and runs the Jumpstarter exporter,
   registering with the existing `jumpstarter-controller`.
4. Instances appear in the pool as regular exporters with the specified labels.
5. Users lease them normally — the existing controller handles assignment.
6. On lease release, the exporter handles internal cleanup. The instance
   returns to the available pool.
7. The controller continuously monitors pool utilization and scales accordingly.

### Failure Modes

- **Pod crash:** Controller detects the failure via Pod status, replaces the
  instance, maintains `minInstances` invariant.
- **Resource exhaustion:** Cannot scale beyond cluster capacity; pool stays at
  current size, new leases queue as they would for physical targets.
- **Provider startup failure:** Instance marked as failed, controller retries
  with backoff, alerts via conditions on the pool status.
- **Scaling storm:** Rate limiting on scale-up prevents creating too many
  instances simultaneously.

## Test Plan

<!-- TODO: Detail specific test cases -->

### Unit Tests
Unit tests should meet the project test coverage requirements.

### Integration Tests

- End-to-end lease lifecycle with a QEMU provider in a test cluster
- Mixed physical/virtual lease orchestration
- Provider failure and recovery scenarios

## Acceptance Criteria

- [ ] `QEMUExporterPool` CRD is defined and validated by the operator
- [ ] Pool controller maintains `minInstances` warm instances for each pool CR
- [ ] Pool controller scales up when available pool is depleted (up to
      `maxInstances`)
- [ ] Pool controller scales down idle instances after cooldown (never below
      `minInstances`)
- [ ] At least one provider (`QEMUExporterPool`) is fully implemented and tested
- [ ] Virtual instances register as standard exporters and are leasable without
      changes to the existing lease flow
- [ ] Pod failures are detected and reported in the pool status.
- [ ] A pool with `minInstances: 0` provisions instances only on demand
- [ ] Pool status subresource reports instance counts and health conditions
- [ ] Documentation covers pool CRD configuration and provider setup
- [ ] Shared scaling logic is reusable for new provider CRDs

## Graduation Criteria

### Experimental

- `QEMUExporterPool` functional in a development cluster
- Basic pool lifecycle works end-to-end (scale up, lease, release, scale down)
- Community feedback on CRD schema and scaling behavior

### Stable

- At least two provider CRDs implemented (e.g., `QEMUExporterPool` +
  `AndroidExporterPool`)
- Production usage by at least one team for >1 month
- Performance benchmarks documented (cold-start latency, scaling responsiveness)
- Provider authoring guide published (how to add a new `*ExporterPool` CRD)

## Backward Compatibility

- Existing physical-only workflows are unaffected; lease requests without
  virtual-specific labels continue to work as before.
- No changes to the existing gRPC protocol for physical exporters.
- New `*ExporterPool` CRDs are additive.
- **Exporter `enabled` field:** Defaults to `true`, so all existing Exporters
  continue to behave exactly as before. The `jumpstarter-controller` must be
  updated to respect this field (skip disabled exporters during lease
  assignment).
- Administrators upgrading to a pool-enabled version see no behavior change
  until they explicitly deploy a `*ExporterPool` resource.

## Consequences

### Positive

- **Instant lease fulfillment:** Warm pools eliminate provisioning latency for
  virtual targets, making CI pipelines faster and more predictable.
- **Elastic scaling:** Pools grow and shrink with demand, avoiding both
  resource waste (idle VMs) and artificial queuing.
- **Unified user experience:** Virtual and physical targets are leased through
  the same mechanism — users do not need to learn a separate workflow.
- **Operator control:** `minInstances` / `maxInstances` give administrators a
  simple, declarative knob to tune the cost-vs-responsiveness trade-off per
  target type.
- **Extensible provider model:** New virtual providers (Renode, Qemu, Corellium, Android,
  etc.) can be added by defining a new CRD and reconciler without modifying
  the core controller or existing providers.

### Negative

- **Increased operator complexity:** Pool controllers, scaling logic, and
  per-provider CRDs add operational surface area — more components to deploy,
  monitor, and debug.
- **Resource consumption:** Warm pools consume cluster resources even when not
  actively leased. Misconfigured `minInstances` can lead to waste.
- **New CRD proliferation:** Each provider type adds a CRD; clusters with
  many providers will have many CRDs to manage and version.

### Risks

- **Scaling storms:** A burst of pending leases could trigger rapid scale-up,
  exhausting cluster resources. Rate limiting mitigates this but may delay
  lease fulfillment under extreme load.
- **Provider startup reliability:** If a virtual provider frequently fails to
  start (e.g., firmware download issues, QEMU misconfiguration), the pool
  controller may enter a tight crash-replace loop, consuming resources without
  making progress.

## Rejected Alternatives

- **Static fixed-size pools (status quo):** Cannot scale with demand. Operators
  must manually adjust pool sizes, leading to either waste or queuing.

- **External orchestration (Terraform/Ansible):** Pushes complexity to the user,
  breaks the single-pane-of-glass experience, and cannot integrate with
  Jumpstarter's lease semantics.

- **Per-lease `parameters` dictionary on the Lease CRD:** Would allow users to
  pass provider-specific resource hints (CPU, memory, storage) per lease.
  Rejected because it adds complexity to every layer (Lease CRD, controller
  pass-through, pool controller parsing, driver template overrides) for a use
  case already served by creating separate pools with different resource
  profiles. See DD-4.

## Prior Art

- **LAVA (Linaro Automated Validation Architecture):** Supports virtual DUTs via
  QEMU but with static configuration; no on-demand scaling.

## Unresolved Questions

- What is the exact scaling algorithm (proportional, step-based, predictive)?

### Resolved

- **Observability (JEP-0013):** Pool controllers and virtual exporter instances
  emit metrics and logs using the same mechanisms defined in JEP-0013.
  Pool-specific metrics (pool size, available/leased counts, scale-up/down
  events) are additional metric series following the same conventions.
- **Lease release detection:** Pool controllers watch Lease objects directly.
  When a Lease referencing one of their managed Exporters is deleted or
  transitions to a released state, the controller triggers scale-down
  evaluation if needed.

## Future Possibilities

The following extensions are explicitly **not** part of this JEP but are
natural follow-ups enabled by the pool infrastructure:

- **Corellium provider:** A `CorelliumExporterPool` CRD that provisions
  virtual instances via the Corellium REST API, with credentials injected
  from Kubernetes Secrets.
- **Renode provider:** A `RenodeExporterPool` CRD leveraging JEP-0010's Renode
  integration as another virtual provider type.

## Implementation Plan

The implementation is broken into phases. Each phase delivers a usable
increment and can be merged independently.

### Phase 1: Exporter `enabled` field

Add the `enabled` boolean field to the Exporter CRD and update the
`jumpstarter-controller` lease assignment logic to skip disabled exporters.

**Deliverables:**

- [ ] Add `spec.enabled` field to Exporter CRD (default: `true`)
- [ ] Update lease assignment in `jumpstarter-controller` to filter out
      disabled exporters
- [ ] Unit tests for the filtering logic
- [ ] Integration test: disable an exporter, verify it gets no new leases

**Why first:** This is a small, self-contained change that is independently
useful for lab operations (maintenance mode) and is a prerequisite for
graceful scale-down in later phases.

### Phase 2: Pool controller scaffold and `QEMUExporterPool` CRD

Build the pool controller binary with the `--provider` flag, define the
`QEMUExporterPool` CRD, and implement the core reconciliation loop.

**Deliverables:**

- [ ] Define `QEMUExporterPool` CRD schema (scaling fields, nodeSelector,
      podTemplate, labels, QEMU-specific fields, exporterTemplate)
- [ ] Implement the pool controller binary with `--provider=qemu` flag
- [ ] Implement core scaling logic: maintain `minInstances`, scale up when
      pool is depleted, graceful scale-down (disable → wait → delete)
- [ ] Instance provisioning: create Pods running the Jumpstarter exporter
      with QEMU provider configuration
- [ ] Instance Pods register as standard Exporter CRs
- [ ] Pool status subresource (totalInstances, readyInstances, leasedInstances,
      conditions)
- [ ] Watch Leases and Exporters for scaling decisions
- [ ] Add `exporterPools` section to the `Jumpstarter` operator CR spec
- [ ] Operator deploys pool controller Deployments based on enabled providers
      (RBAC, service accounts, Deployment lifecycle)
- [ ] Unit tests for reconciliation logic
- [ ] Integration test: deploy a `QEMUExporterPool`, verify instances come up,
      lease one, release it, observe scale behavior

### Phase 3: Additional providers

Add support for additional provider types using the same binary with different
`--provider` flags.

**Deliverables:**

- [ ] `AndroidExporterPool` CRD and reconciler
- [ ] Provider authoring guide documenting how to add a new `*ExporterPool`

## Implementation History

- 2025-10-30: RFE filed upstream (GitHub #41)
- 2026-06-03: JEP proposed

## References

- [GitHub Issue #41: RFE: On-Demand Virtual Target Provisioning](https://github.com/jumpstarter-dev/jumpstarter/issues/41)
- [PITCREW-409: jumpstarter JEP: virtual scalable exporters](https://redhat.atlassian.net/browse/PITCREW-409)
- [JEP-0010: Renode Integration](JEP-0010-renode-integration.md) — Related provider
- [JEP-0013: Observability](JEP-0013-observability-telemetry-logs.md) — Integration point

---

This JEP is licensed under the
[Apache License, Version 2.0](https://www.apache.org/licenses/LICENSE-2.0),
