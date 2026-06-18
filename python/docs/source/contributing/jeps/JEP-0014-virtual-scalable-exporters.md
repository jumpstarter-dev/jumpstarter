# JEP-0014: Virtual Scalable Exporters

| Field             | Value                                                          |
| ----------------- | -------------------------------------------------------------- |
| **JEP**           | 0014                                                           |
| **Title**         | Virtual Scalable Exporters                                     |
| **Author(s)**     | @mangelajo (Miguel Angel Ajo Pelayo)                           |
| **Status**        | Draft                                                          |
| **Type**          | Standards Track                                                |
| **Created**       | 2026-06-03                                                     |
| **Updated**       | 2026-06-18                                                     |
| **Discussion**    | https://github.com/jumpstarter-dev/jumpstarter/issues/41       |
| **Requires**      |                                                                |
| **Supersedes**    |                                                                |
| **Superseded-By** |                                                                |

---

## Abstract

This JEP proposes a Virtual Scalable Exporter subsystem for Jumpstarter that
manages pools of virtual targets with configurable autoscaling. Conceptually,
the system scales **virtual targets**; the **Exporter** is the scheduling and
leasing unit (the Pod analog). Each `ExporterSet` declares scaling bounds using
familiar Kubernetes vocabulary (`minReplicas`, `maxReplicas`,
`minAvailableReplicas`); the controller maintains a warm pool of ready exporters
to absorb the 10-60s cold-start latency of VM boot and exporter registration.
This enables low-latency lease acquisition, massive scalability, resource
efficiency, and simplified orchestration of mixed physical/virtual test
topologies — while allowing administrators to tune the trade-off between
responsiveness and resource consumption on a per-target basis.

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

### Fidelity / Cost Ladder

One logical target can be served by multiple backends at different fidelity and
cost tiers. Users select via labels through `jmp lease`; the same workflow
applies regardless of backend:

| class (provisioner) | fidelity | scale/cost | role |
| --- | --- | --- | --- |
| container sim (`qemu.jumpstarter.dev`) | low | cheap / CI-scale | functional checks |
| cloud virtual device (`corellium.jumpstarter.dev`) | high | metered | higher-fidelity behavior |
| real hardware (Exporter) | full | scarce | ground truth |

For example, a target that needs GPU or specialized I/O can run functional
checks cheaply on a QEMU class in CI, validate higher-fidelity behavior on a
cloud-backed virtual device, and use real hardware as ground truth. The
`VirtualTargetClass` / `*VirtualTarget` abstraction makes this ladder explicit
without changing the lease experience.

### User Stories

- **As a** CI pipeline author, **I want to** lease N virtual targets instantly
  from a warm pool, **so that** my pipeline doesn't block on provisioning
  latency during burst periods.

- **As a** developer, **I want to** lease a virtual target matching a known
  physical board's properties with near-zero wait time, **so that** I can
  iterate quickly without waiting for scarce hardware.

- **As a** platform engineer, **I want to** declare an `ExporterSet` with
  `minAvailableReplicas: 2, maxReplicas: 20`, **so that** there are always warm
  instances ready while the system scales up on demand and scales down when idle.

- **As a** cost-conscious operator, **I want to** set `minAvailableReplicas: 0`
  for rarely-used target types, **so that** they consume no resources until
  actually requested, accepting a cold-start delay.

## Proposal

The proposal introduces **Virtual Scalable Exporters** — a controller-managed
pool of virtual target instances with configurable autoscaling. Rather than
treating virtual targets as purely on-demand or purely static, each
`ExporterSet` declares scaling parameters that let administrators tune the
trade-off between instant availability and resource consumption.

### Resource Hierarchy

Virtual scalable exporters are modeled on familiar Kubernetes workload
primitives:

```text
VirtualTargetClass  ←── referenced by ──  ExporterSet
                                              │
                                              ▼
                                         Exporter ──► Pod
                              (exporter sidecar + target runtime)

# API-backed / static / multi-device cases may also use:
VirtualTargetClass  ←── referenced by ──  *VirtualTarget (typed claim)
                                              ↑
ExporterSet ──► Exporter ────────────────────┘
```

- **`VirtualTargetClass`** — cluster-scoped configuration for a backend
  (`provisioner`, credentials, scheduling, binding mode, device parameters).
  Admins own classes; claim authors never touch credentials.
- **`*VirtualTarget`** — optional strongly-typed claim for backends where each
  instance has distinct identity (API-backed devices, static benches). Not
  required for homogeneous container-backed pools.
- **`ExporterSet`** — generic scaling resource with `selector` + inline
  `template`. References a `VirtualTargetClass` (or optionally a `*VirtualTarget`
  claim). One mental model for all backends.
- **`Exporter`** — the minimum leased unit. Exposes drivers that connect to the
  virtual target provisioned from the class (or claim).

### Core Concept: ExporterSet with Kubernetes-Native Scaling

`ExporterSet` is a generic CRD (ReplicaSet + HPA analog) with familiar scaling
vocabulary. Provider typing lives in `VirtualTargetClass` and `*VirtualTarget`,
not in the pool CRD itself.

**Example: VirtualTargetClass (cluster-scoped, StorageClass analog)**

```yaml
apiVersion: jumpstarter.dev/v1alpha1
kind: VirtualTargetClass
metadata:
  name: qemu-rpi4
spec:
  provisioner: qemu.jumpstarter.dev
  bindingMode: Immediate              # warm pool; WaitForFirstConsumer = on-demand
  reclaimPolicy: Delete
  scheduling:                         # inherited by rendered exporter Pods
    nodeSelector:
      kubernetes.io/arch: arm64
    tolerations:
      - key: jumpstarter.dev/kvm
        operator: Exists
        effect: NoSchedule
    resources:
      limits:
        devices.kubevirt.io/kvm: "1"
  parameters:
    machineType: virt
    firmware: registry.example.com/firmware/rpi4:latest
    cpu: 4
    memory: 4Gi
    storage: 16Gi
```

**Example: QEMUVirtualTarget (optional typed claim)**

For homogeneous QEMU pools, admins configure `VirtualTargetClass` + `ExporterSet`
only (see *End-to-End Flow*). A per-instance claim is optional — useful for
static benches or when per-instance sizing differs from the class defaults:

```yaml
apiVersion: jumpstarter.dev/v1alpha1
kind: QEMUVirtualTarget
metadata:
  name: rpi4-target-01
  namespace: jumpstarter
spec:
  virtualTargetClassName: qemu-rpi4
  resources:
    cpu: 8                           # override class default
    memory: 8Gi
    storage: 32Gi
```

**Example: ExporterSet (generic scaling resource)**

```yaml
apiVersion: jumpstarter.dev/v1alpha1
kind: ExporterSet
metadata:
  name: rpi4-virtual
  namespace: jumpstarter
spec:
  minReplicas: 0
  maxReplicas: 20
  minAvailableReplicas: 2            # PDB-style warm buffer (ready & unleased)
  scaleDownCooldown: 5m
  recycleStrategy: ExitAndReplace    # or InPlaceReuse
  virtualTargetClassName: qemu-rpi4  # references VirtualTargetClass above
  selector:
    matchLabels:
      board: rpi4
  template:                          # embedded template (Deployment idiom)
    metadata:
      labels:
        board: rpi4
        arch: aarch64
        virtual: "true"
    spec:
      drivers:
        - type: jumpstarter_driver_power.driver.QemuPower
        - type: jumpstarter_driver_network.driver.TcpNetwork
          config:
            port: 22
        - type: jumpstarter_driver_serial.driver.QemuSerial
status:
  replicas: 5
  readyReplicas: 3
  availableReplicas: 1             # warm (ready & unleased)
  leasedReplicas: 2
# scale subresource: specReplicasPath=.spec.maxReplicas
```

**Example: Corellium VirtualTargetClass + claim**

```yaml
apiVersion: jumpstarter.dev/v1alpha1
kind: VirtualTargetClass
metadata:
  name: corellium-kronos
spec:
  provisioner: corellium.jumpstarter.dev
  credentialsSecretRef:
    name: corellium-creds
    namespace: jumpstarter
  bindingMode: WaitForFirstConsumer  # provision on lease
  reclaimPolicy: Delete
  parameters:
    apiHost: app.corellium.com
    projectId: "778f00af-5e9b-40e6-8e7f-c4f14b632e9c"
---
apiVersion: jumpstarter.dev/v1alpha1
kind: CorelliumVirtualTarget
metadata:
  name: rd1ae-kronos-01
  namespace: jumpstarter
spec:
  virtualTargetClassName: corellium-kronos
  deviceFlavor: kronos
  deviceOs: "1.1.1"
  deviceBuild: "Critical Application Monitor (Baremetal)"
  consoleName: "Primary Compute Non-Secure"
```

The Corellium driver (`jumpstarter_driver_corellium.driver.Corellium`) manages
the full virtual instance lifecycle through the Corellium REST API — it creates
instances on power-on and destroys them on power-off. The provisioner injects
API credentials from `VirtualTargetClass.credentialsSecretRef` into the
exporter Pod; claim authors never see credentials.

**Example: Android ExporterSet**

```yaml
apiVersion: jumpstarter.dev/v1alpha1
kind: ExporterSet
metadata:
  name: pixel7-emulator
  namespace: jumpstarter
spec:
  minReplicas: 0
  maxReplicas: 10
  minAvailableReplicas: 0            # fully on-demand
  selector:
    matchLabels:
      device: pixel7
  template:
    metadata:
      labels:
        device: pixel7
        os: android
        api-level: "34"
        virtual: "true"
    spec:
      virtualTargetRef:
        apiVersion: jumpstarter.dev/v1alpha1
        kind: AndroidVirtualTarget
        name: pixel7-template
      drivers:
        - type: jumpstarter_driver_android.driver.AdbDriver
        - type: jumpstarter_driver_power.driver.EmulatorPower
```

An `ExporterSet` with `minAvailableReplicas: 0` consumes no resources until a
lease is requested, accepting cold-start latency. An `ExporterSet` with
`minAvailableReplicas: 3` always has 3 ready-to-lease exporters — leases are
fulfilled instantly from the warm pool, and the controller scales up if more are
needed.

### Container-Backed Targets: Sidecar Pattern

For container-backed provisioners (`qemu.jumpstarter.dev`, Android emulator, etc.),
the provisioner renders each instance Pod from independently shipped artifacts:

```yaml
# rendered by qemu.jumpstarter.dev provisioner
spec:
  initContainers:
    - name: exporter                 # native sidecar (starts first, drains last)
      restartPolicy: Always
      image: quay.io/jumpstarter-dev/exporter:latest
  containers:
    - name: target-runtime           # QEMU/Cuttlefish — independent image
      image: quay.io/jumpstarter-dev/qemu-runtime:latest
      volumeMounts:
        - name: os
          mountPath: /os
        - name: shared
          mountPath: /shared
  volumes:
    - name: os
      image:
        reference: registry.example.com/os/rpi4:latest   # OS as OCI artifact
    - name: shared
      emptyDir: {}
```

Benefits:

- **Independent release cadence** — exporter, runtime, and OS image version
  independently.
- **Fault isolation** — exporter survives target-runtime crashes and can drain
  or report failure.
- **Standard interfaces** — drivers attach over virtio (serial/SPI/CAN/GPIO) or
  Unix sockets on shared volumes; same driver code works physical + virtual.
- **Unprivileged Pods** — virtio-backed guests avoid privileged containers when
  the host supports it.

The exporter sidecar communicates with the target-runtime container via Unix
sockets on a shared `emptyDir` volume (QMP for QEMU control, serial console,
launcher socket for dynamic argv). API-backed provisioners (`corellium`, `ec2`)
skip the runtime container and connect out to external APIs.

### User Experience

From the user's perspective, virtual scalable exporters appear as regular
exporters in the pool. The lease experience is unchanged:

```bash
# Lease any rpi4 target — may match physical or virtual
jmp lease -l board=rpi4

# Lease explicitly virtual targets
jmp lease -l board=rpi4,virtual=true

# Prefer ground truth when fidelity matters
jmp lease -l board=rpi4,fidelity=full
```

The guiding principle is: **"Get me a target that matches my requirements."** The
distinction between physical and virtual is an implementation detail, not a
primary concern for the user. Virtual exporters simply appear in the same pool
as physical ones, differentiated only by labels.

### End-to-End Flow (QEMU Example)

This section walks through a complete QEMU warm-pool scenario: what each actor
does, which CRDs are involved, and how control passes between components. It
uses the **reference graph** (not a strict ownership tree) for relationships
between resources:

```text
VirtualTargetClass  ←── referenced by ──  ExporterSet
                                              │
                                              ▼
                                         Exporter ──► Pod
                              (exporter sidecar + QEMU runtime)
```

For **homogeneous QEMU pools** (same CPU/RAM/disk for every replica, no
per-lease parameterization), configuration flows through `VirtualTargetClass` +
`ExporterSet` only. The provisioner materializes Pods from those two resources;
per-instance `QEMUVirtualTarget` claims are **not** required in this case (they
remain useful for API-backed backends, static benches, or future multi-device
exporters — see *Future Possibilities*).

#### Actors

| Actor | Component | Responsibility |
| --- | --- | --- |
| **Administrator** | Human / GitOps | Cluster bootstrap, class + set CRs |
| **Jumpstarter operator** | `Jumpstarter` CR | Deploys `jumpstarter-controller`, routers, exporter-set controllers |
| **Exporter-set controller** | `qemu.jumpstarter.dev` Deployment | Reconciles `ExporterSet`, creates Exporters/Pods, scales pool |
| **Jumpstarter controller** | Existing controller | Assigns `Lease` → `Exporter`, unchanged lease semantics |
| **User** | CLI / CI (`jmp lease`, drivers) | Requests leases, flashes images, runs tests |

#### Phase 0 — Cluster bootstrap (admin, one-time)

**Admin actions:**

1. Install Jumpstarter operator (if not already present).
2. Configure the `Jumpstarter` CR with `spec.exporterSets.provisioners` listing
   `qemu.jumpstarter.dev` (and any other provisioners).

**Controller actions:**

- Operator creates the exporter-set controller Deployment
  (`--provisioner=qemu.jumpstarter.dev`).
- Operator ensures `jumpstarter-controller` is running (existing behavior).

**Result:** Provisioner controller is watching for `ExporterSet` CRs whose
templates reference QEMU virtual targets (via `virtualTargetClassName` or
`*VirtualTarget` claims).

#### Phase 1 — Define the virtual target profile (admin)

**Admin actions:**

1. Create a cluster-scoped `VirtualTargetClass` describing the QEMU backend:

```yaml
apiVersion: jumpstarter.dev/v1alpha1
kind: VirtualTargetClass
metadata:
  name: qemu-rpi4
spec:
  provisioner: qemu.jumpstarter.dev
  bindingMode: Immediate
  reclaimPolicy: Delete
  scheduling:
    nodeSelector:
      kubernetes.io/arch: arm64
    tolerations:
      - key: jumpstarter.dev/kvm
        operator: Exists
        effect: NoSchedule
    resources:
      limits:
        devices.kubevirt.io/kvm: "1"
  parameters:
    machineType: virt
    firmware: registry.example.com/firmware/rpi4:latest
    cpu: 4
    memory: 4Gi
    storage: 16Gi
```

2. Create an `ExporterSet` that references the class and declares scaling +
   lease-matching labels:

```yaml
apiVersion: jumpstarter.dev/v1alpha1
kind: ExporterSet
metadata:
  name: rpi4-virtual
  namespace: jumpstarter
spec:
  minReplicas: 0
  maxReplicas: 20
  minAvailableReplicas: 2
  scaleDownCooldown: 5m
  recycleStrategy: ExitAndReplace
  virtualTargetClassName: qemu-rpi4
  selector:
    matchLabels:
      board: rpi4
  template:
    metadata:
      labels:
        board: rpi4
        arch: aarch64
        virtual: "true"
    spec:
      drivers:
        - type: jumpstarter_driver_power.driver.QemuPower
        - type: jumpstarter_driver_network.driver.TcpNetwork
          config:
            port: 22
        - type: jumpstarter_driver_serial.driver.QemuSerial
```

**User actions:** None.

**Controller actions:** None yet (waiting for `ExporterSet` to be observed).

#### Phase 2 — Warm pool provisioning (exporter-set controller)

**Trigger:** `ExporterSet` CR created or updated; `minAvailableReplicas: 2`.

**Exporter-set controller actions (reconcile loop):**

1. Read `ExporterSet` spec and referenced `VirtualTargetClass`.
2. Count owned `Exporter` CRs: `replicas`, `readyReplicas`, `leasedReplicas`,
   `availableReplicas` (= ready − leased).
3. If `availableReplicas < minAvailableReplicas` and `replicas < maxReplicas`,
   scale up by creating new instances. For each new instance:
   - Create an `Exporter` CR with labels from `spec.template.metadata` and
     drivers from `spec.template.spec`.
   - Render a Kubernetes Pod (sidecar pattern):
     - **Exporter sidecar** (native sidecar, `restartPolicy: Always`) — starts
       first, registers with `jumpstarter-controller`.
     - **QEMU runtime container** — started by provisioner; exporter talks to
       it via Unix sockets on a shared `emptyDir` (QMP, serial, launcher).
   - Apply scheduling from `VirtualTargetClass.scheduling` to the Pod.
   - Apply device parameters from `VirtualTargetClass.parameters` when
     constructing the QEMU command line.
4. Update `ExporterSet.status` (`replicas`, `readyReplicas`, `availableReplicas`,
   `leasedReplicas`, conditions).

**Jumpstarter-controller actions:**

- Accepts exporter registrations from the sidecar processes (existing gRPC flow).
- Marks exporters as available for lease assignment when ready.

**User actions:** None.

**Result:** Two warm exporters appear in the pool, labeled `board=rpi4,
virtual=true`. `ExporterSet.status.availableReplicas: 2`.

```text
ExporterSet rpi4-virtual
├── Exporter rpi4-virtual-aaa   [Ready, unleased]  →  Pod (exporter + QEMU)
└── Exporter rpi4-virtual-bbb   [Ready, unleased]  →  Pod (exporter + QEMU)
```

#### Phase 3 — User requests a lease (user + jumpstarter-controller)

**User actions:**

```bash
jmp lease -l board=rpi4,virtual=true
```

**Jumpstarter-controller actions:**

1. Create a `Lease` CR with `spec.selector.matchLabels: {board: rpi4, virtual: "true"}`.
2. Scan available `Exporter` CRs matching the selector (enabled, no active
   `leaseRef`, ready).
3. Pick one (e.g. `rpi4-virtual-aaa`) and set `Exporter.status.leaseRef` to the
   lease name.
4. Return connection details to the user (existing flow).

**Exporter-set controller actions:**

- Observes `leasedReplicas` increased, `availableReplicas` decreased.
- If `availableReplicas < minAvailableReplicas`, begins scale-up (create another
  instance to refill the warm buffer).
- Does **not** participate in lease assignment.

**Result:** User holds an active lease on `rpi4-virtual-aaa`. Pool still
maintains warm capacity via background scale-up.

#### Phase 4 — User session (user + exporter sidecar)

**User actions** (via leased client — same as physical targets):

```python
with env() as client:
    client.storage.flash("/path/to/image.raw")   # write disk image
    client.power.on()                             # boot QEMU via QemuPower driver
    client.serial.read()                          # interact over serial
    # ... run tests ...
```

**Exporter sidecar actions:**

- `storage.flash` writes the image to shared storage (or tells QEMU runtime via
  QMP/`blockdev-add` in sidecar mode).
- `power.on` sends QEMU start via QMP or launcher socket on shared volume.
- Serial/network drivers proxy to the QEMU runtime container.

**Controller actions:** None during the session (lease is held).

#### Phase 5 — Lease release and recycle (user + controllers)

**User actions:**

```bash
jmp delete-lease <lease-id>    # or lease TTL expires
```

**Jumpstarter-controller actions:**

1. Clear `Exporter.status.leaseRef` on `rpi4-virtual-aaa`.
2. Mark lease as released.

**Exporter-set controller actions:**

1. Observe exporter is unleased; update `availableReplicas` / `leasedReplicas`.
2. Apply `recycleStrategy`:
   - **ExitAndReplace (default):** exporter sidecar exits after cleanup → Pod
     terminates → controller deletes `Exporter` CR → creates a fresh replacement
     to maintain `minAvailableReplicas`.
   - **InPlaceReuse:** exporter resets QEMU state in place → same Pod returns
     to Ready without restart.
3. If `availableReplicas > minAvailableReplicas` for longer than
   `scaleDownCooldown`, gracefully scale down an excess replica:
   - Set `Exporter.spec.enabled: false`
   - Wait until no lease assigned
   - Delete Pod + `Exporter` CR

**Result:** Pool returns to steady state with `minAvailableReplicas` warm,
unleased exporters.

#### Phase 6 — Demand spike (scale-up under load)

**Trigger:** Three users (or CI jobs) request leases simultaneously; only one
warm exporter remains.

**User actions:** Three concurrent `jmp lease -l board=rpi4,virtual=true`.

**Jumpstarter-controller actions:**

- Assigns the one available exporter immediately.
- Sets `Pending` condition on the other two leases (existing behavior when no
  exporter is available).

**Exporter-set controller actions:**

1. Sees pending leases matching `spec.selector` with no available exporters.
2. Scales up: creates new `Exporter` + Pod instances (up to `maxReplicas`).
3. As new exporters register and become ready, jumpstarter-controller assigns
   pending leases.

**Result:** Pool grows to meet demand, then shrinks back after cooldown when
leases are released.

#### Summary: who touches which CRD

| CRD | Created by | Observed by | User-visible? |
| --- | --- | --- | --- |
| `Jumpstarter` | Admin | Operator | No |
| `VirtualTargetClass` | Admin | Exporter-set controller | No |
| `ExporterSet` | Admin | Exporter-set controller | No (admin/kubectl) |
| `Exporter` | Exporter-set controller | Jumpstarter-controller, exporter-set controller | Indirectly (via lease) |
| `Lease` | User (via CLI) | Jumpstarter-controller, exporter-set controller | Yes |
| `Pod` | Exporter-set controller | Kubernetes, exporter-set controller | No |

#### QEMU vs API-backed backends

The flow above applies to **container-backed** provisioners (`qemu.jumpstarter.dev`).
For **API-backed** backends (e.g. `corellium.jumpstarter.dev`):

- `VirtualTargetClass` holds `credentialsSecretRef` and API parameters.
- A typed `*VirtualTarget` claim (e.g. `CorelliumVirtualTarget`) may be created
  per instance when the backend provisions an external device with its own
  lifecycle and identity.
- The exporter Pod is lighter (API client only; no QEMU runtime container).

The `ExporterSet` + `jumpstarter-controller` lease flow is identical.

### Architecture Overview

```text
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
                  │  (Lease, Exporter, ExporterSet,   │
                  │   VirtualTargetClass, *VirtualTarget)│
                  └─┬──────────────┬──────────────┬────┘
                    │              │              │
         watches    │   watches    │   watches    │
      Leases +      │  Leases +    │  Leases +    │
      Exporters     │  Exporters   │  Exporters   │
                    │              │              │
  ┌─────────────────▼┐ ┌───────────▼──────────┐┌──▼──────────────────────┐
  │ qemu provisioner │ │ android provisioner  │ │ corellium provisioner   │
  │ (ExporterSet     │ │ (ExporterSet         │ │ (ExporterSet            │
  │  controller)     │ │  controller)         │ │  controller)            │
  └────────┬─────────┘ └──────────┬──────────┘ └────────────┬────────────┘
           │                      │                         │
           │ manages              │ manages                 │ manages
           ▼                      ▼                         ▼
  ┌──────────────────┐ ┌───────────────────────┐ ┌────────────────────────┐
  │ Warm Pool        │ │ Warm Pool             │ │ Warm Pool              │
  │ [Exporter]..     │ │ [Exporter]..          │ │ [Exporter]..           │
  └────────┬─────────┘ └───────────┬───────────┘ └────────────┬───────────┘
           │                       │                          │
           └───────────────────────┼──────────────────────────┘
                                   │ register as standard Exporter CRs
                                   ▼
                          Kubernetes API (Exporters)
```

**Scaling Inputs — Watches on Leases and Exporters:**

Each `ExporterSet` controller watches two key resources to make scaling decisions:

1. **Leases** — The controller watches for pending Leases whose label selectors
   match the set's selector. Pending leases with no available exporter signal
   demand and trigger scale-up.
2. **Exporters** — The controller watches owned Exporter objects to track which
   instances are available (no active lease) vs. occupied (leased). This
   determines the current pool utilization.

Together these inputs feed the scaling logic: if there are pending leases that
match this set and no available instances to serve them, scale up. If there are
excess idle instances beyond `minAvailableReplicas` for a sustained period, scale
down.

**Per-Provisioner Deployments (single image by default):** All provisioner
controllers are compiled into a single binary. Each Deployment in the cluster
passes a `--provisioner=<name>` flag to activate the corresponding reconciler
(e.g., `qemu.jumpstarter.dev`). This gives each provisioner isolated logs and
independent restarts while maintaining a single image to build and release.

The Jumpstarter operator deploys provisioner controllers based on the
`Jumpstarter` CR configuration. A new `exporterSets` section lists which
provisioners to enable:

```yaml
apiVersion: operator.jumpstarter.dev/v1alpha1
kind: Jumpstarter
metadata:
  name: jumpstarter
  namespace: jumpstarter
spec:
  # ... existing controller, routers, authentication config ...
  
  exporterSets:
    image: quay.io/jumpstarter-dev/exporter-set-controller:latest
    imagePullPolicy: IfNotPresent
    provisioners:
      - name: qemu.jumpstarter.dev
        enabled: true
      - name: corellium.jumpstarter.dev
        enabled: false
        image: quay.io/jumpstarter-dev/exporter-set-controller-corellium:latest
```

**Scaling Logic:** Each `ExporterSet` controller monitors its instances and scales
based on available (unleased) replicas:

- If `availableReplicas` drops below `minAvailableReplicas`, scale up.
- If `availableReplicas` exceeds demand for a cooldown period, scale down (never
  below `minAvailableReplicas`).
- Never exceed `maxReplicas` (if set; 0 or omitted means no upper bound).
- `kubectl scale exporterset/<name> --replicas=N` works via the `scale`
  subresource (`specReplicasPath=.spec.maxReplicas`).

**Instance Lifecycle:**

1. `ExporterSet` controller creates an Exporter + `*VirtualTarget` from the set
   template (provisioner renders the Pod).
2. The Pod starts the virtual target (sidecar pattern for container backends, or
   API call for external backends) and runs the Jumpstarter exporter, registering
   with the controller like any other exporter.
3. The instance becomes available in the pool for lease assignment.
4. When a lease is released, the exporter handles cleanup/reset per
   `recycleStrategy`. The instance returns to the available pool or is replaced.

### API / Protocol Changes

**New CRDs**

| CRD | Scope | Role |
| --- | --- | --- |
| `VirtualTargetClass` | Cluster | StorageClass analog — provisioner, credentials, scheduling, binding |
| `QEMUVirtualTarget` | Namespaced | Typed claim for QEMU backends |
| `CorelliumVirtualTarget` | Namespaced | Typed claim for Corellium backends |
| `AndroidVirtualTarget` | Namespaced | Typed claim for Android emulator backends |
| `ExporterSet` | Namespaced | Generic scaling resource (ReplicaSet + HPA analog) |

**VirtualTargetClass (common fields):**

```yaml
spec:
  provisioner: <string>              # e.g. qemu.jumpstarter.dev
  credentialsSecretRef:              # optional; for API-backed provisioners
    name: <string>
    namespace: <string>
  parameters:                        # opaque to orchestration; provisioner-specific
    <key>: <value>
  bindingMode: Immediate | WaitForFirstConsumer
  reclaimPolicy: Delete | Retain
  scheduling:                        # inherited by rendered exporter Pods
    nodeSelector:
      <key>: <value>
    nodeAffinity: { ... }
    tolerations: [ ... ]
    resources:
      limits:
        devices.kubevirt.io/kvm: "1"
```

**ExporterSet (common fields):**

```yaml
spec:
  minReplicas: <int>                 # floor (default: 0)
  maxReplicas: <int>                 # ceiling (0 or omitted = no limit)
  minAvailableReplicas: <int>        # warm buffer: ready & unleased (default: 0)
  scaleDownCooldown: <duration>      # default: 5m
  recycleStrategy: ExitAndReplace | InPlaceReuse
  selector:
    matchLabels:
      <key>: <value>
  template:
    metadata:
      labels: { ... }
    spec:
      virtualTargetRef: { ... }      # reference or inline *VirtualTarget spec
      drivers: [ ... ]
```

**Status subresource (ExporterSet):**

```yaml
status:
  replicas: 5
  readyReplicas: 3
  availableReplicas: 1               # warm (ready & unleased)
  leasedReplicas: 2
  conditions:
    - type: SetHealthy
      status: "True"
    - type: ScalingLimited
      status: "False"
```

**Scale subresource:** `specReplicasPath=.spec.maxReplicas` enables
`kubectl scale` and HPA/KEDA interoperability.

**Pluggable provisioners:**

```text
VirtualTargetClass.provisioner →
  qemu.jumpstarter.dev        →  k8s container (+ OS OCI image volume)
  ec2.jumpstarter.dev         →  AWS API
  corellium.jumpstarter.dev   →  Corellium REST API
# one typed *VirtualTarget claim interface; backend is pluggable
```

**Changes to existing CRDs:**

**Exporter — new `enabled` field:**

Exporters gain an `enabled` boolean field (default: `true`). When set to
`false`, the `jumpstarter-controller` will not assign new leases to this
exporter. This is useful for:

- **Lab operations:** Temporarily taking a physical exporter offline for
  maintenance without deleting it.
- **Graceful scale-down:** `ExporterSet` controllers set `enabled: false` before
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

1. `ExporterSet` controller sets `enabled: false` on the target exporter.
2. Controller waits to confirm no lease was assigned (watches for
   `status.leaseRef` to remain empty).
3. Controller deletes the Pod, Exporter CR, and associated `*VirtualTarget`.

### Hardware Considerations

This proposal is specifically designed to reduce reliance on physical hardware
for scalable testing. However:

- Virtual targets must faithfully emulate the interfaces exposed by physical
  hardware (serial, network, storage, power) through the existing driver model.
- Container-backed provisioners require `/dev/kvm` or equivalent; scheduling is
  expressed on `VirtualTargetClass.scheduling`.
- Timing-sensitive tests (USB/IP latency, boot ROM timeouts) may behave
  differently on virtual targets — the system should expose labels indicating
  whether a target is physical or virtual so users can filter when fidelity
  matters.

## Design Decisions

### DD-1: Pool-based scaling vs. purely on-demand provisioning

**Alternatives considered:**

1. **Pool-based with configurable min/max** — Maintain a warm pool of
   pre-spawned instances; scale between `minAvailableReplicas` and `maxReplicas`.
2. **Purely on-demand** — Spawn a new instance only when a lease request arrives;
   destroy it when the lease is released.

**Decision:** Pool-based with configurable min/max.

**Rationale:** Purely on-demand provisioning introduces noticeable latency for
CI pipelines (Pod scheduling + image pull + VM boot + exporter registration
typically takes 10-15s, and up to 60s with cold image pulls or heavy
provisioners). A warm pool provides instant lease fulfillment for the common
case. Setting `minAvailableReplicas: 0` still allows purely on-demand behavior
for rarely-used targets. `VirtualTargetClass.bindingMode: WaitForFirstConsumer`
maps to on-demand provisioning; `Immediate` maps to warm pools.

### DD-2: Provisioner controller deployment model

**Alternatives considered:**

1. **Separate binary per provisioner** — Each provisioner is a completely
   independent binary/image.
2. **Single binary, one deployment per provisioner** — One image contains all
   provisioner reconcilers; a CLI flag (`--provisioner=qemu.jumpstarter.dev`)
   selects which one to activate.
3. **Single binary, single deployment** — One Deployment runs all provisioners.
4. **Integrated into jumpstarter-controller** — Add reconcilers directly into
   the existing operator.

**Decision:** Option 2 — single binary, one Deployment per provisioner.

**Rationale:** A single image is cheaper to build, test, and productize.
Deploying as separate Deployments gives operational benefits: isolated logs,
independent restarts, and explicit `--provisioner` selection. Adding a new
backend means adding a Deployment manifest with a different flag — no new image
build required.

### DD-3: Pluggable provisioner vs. CRD-per-pool

**Alternatives considered:**

1. **CRD per provider pool** (`QEMUExporterPool`, `AndroidExporterPool`, etc.)
   — provider typing at the pool CRD level.
2. **Generic `ExporterSet` + pluggable `VirtualTargetClass.provisioner`** —
   orchestration generic; device backend selected by provisioner string; typed
   `*VirtualTarget` claims retain strong typing.
3. **Fully generic opaque config** — single CRD with `provider.config` map.

**Decision:** Option 2 — generic `ExporterSet` + pluggable provisioner on
`VirtualTargetClass`, with typed `*VirtualTarget` claims.

**Rationale:** Separating orchestration (scaling, lease matching, graceful
shutdown) from provisioning (QEMU container, Corellium API, EC2) lets each
provisioner implement backend-appropriate scaling logic while exposing an
identical scaling surface (`minReplicas`/`maxReplicas`/`minAvailableReplicas`).
Typed `*VirtualTarget` claims preserve schema validation per provider without
proliferating pool CRDs. New backends add a claim kind + provisioner string, not
pool-tier changes.

### DD-4: Per-lease parameters vs. pool flavors

**Alternatives considered:**

1. **Per-lease `parameters` dictionary** — Leases carry opaque hints (CPU,
   memory, storage) interpreted by provisioners.
2. **Multiple `ExporterSet` flavors** — Administrators create separate sets for
   different resource profiles; users select via label matching.

**Decision:** Option 2 — multiple set flavors via separate `ExporterSet` CRs.

**Rationale:** Per-lease parameters add complexity across every layer for a use
case already satisfied by separate sets with different labels and
`VirtualTargetClass` parameters. Per-lease parameters can be revisited in a
future JEP if needed.

### DD-5: Built-in scaling vs. HPA / KEDA

**Alternatives considered:**

1. **Built-in scaling logic** — Each provisioner implements lease-aware
   reconciliation with a consistent scaling API.
2. **Kubernetes HPA** — Horizontal Pod Autoscaler with custom metrics.
3. **KEDA** — Event-driven autoscaler with a custom Jumpstarter scaler.

**Decision:** Option 1 — built-in scaling logic with consistent API surface;
HPA/KEDA as complementary via `scale` subresource and exposed metrics.

**Rationale:** Each provisioner should implement autoscaling appropriate to its
backend (local container churn vs. EC2 quotas vs. external API rate limits). A
single generic autoscaler cannot express lease-aware matching, graceful
disable-before-delete, or `minAvailableReplicas` invariants. However, the
**same scaling vocabulary** (`minReplicas`/`maxReplicas`/`minAvailableReplicas`)
and the `scale` subresource apply across all provisioners — one mental model for
users, backend-specific logic underneath. Pool metrics for HPA/KEDA are listed
in *Future Possibilities*.

### DD-6: VirtualTargetClass vs. inline credentials

**Alternatives considered:**

1. **Inline credentials in every `ExporterSet`** — simple but duplicates secrets
   across pools sharing the same backend account.
2. **`VirtualTargetClass` (StorageClass analog)** — cluster-scoped class holds
   credentials, parameters, scheduling; claims reference the class.
3. **Separate `ProviderConfig` CRD** — lighter-weight credential sharing without
   full class semantics.

**Decision:** Option 2 — `VirtualTargetClass` with optional future
`ProviderConfig` for multi-account credential reuse.

**Rationale:** The CSI StorageClass/PVC pattern is well understood by cluster
admins. `bindingMode` and `reclaimPolicy` map naturally to warm-pool vs.
on-demand and expensive external target retention. Credentials never appear on
namespaced claims.

## Design Details

### Reconciliation Loop

Each `ExporterSet` controller runs a continuous reconciliation loop, triggered by
changes to the set CR, owned Exporters, or matching Leases:

```text
for each ExporterSet CR:
  ownedExporters = list Exporters owned by this CR
  replicas = count ownedExporters in Ready state
  leasedReplicas = count ownedExporters with an active LeaseRef
  availableReplicas = replicas - leasedReplicas
  pendingLeases = count pending Leases matching spec.selector
  
  # Invariant: maintain minAvailableReplicas warm buffer
  if availableReplicas < spec.minAvailableReplicas AND replicas < spec.maxReplicas:
    scale up to restore availableReplicas

  # Demand-driven scale-up
  elif pendingLeases > 0 AND replicas < spec.maxReplicas:
    scale up by min(pendingLeases, spec.maxReplicas - replicas)

  # Scale-down: excess idle replicas
  elif availableReplicas > spec.minAvailableReplicas AND cooldown elapsed:
    graceful scale down:
      1. set exporter.spec.enabled = false
      2. wait until leaseRef remains empty
      3. delete Pod, Exporter CR, and *VirtualTarget
    (never below minAvailableReplicas)
```

### Instance States

Each virtual exporter instance transitions through:

```text
Provisioning → Ready (warm pool) → Leased → Ready
                                              └→ Terminating → (deleted if available>min)
```

- **Provisioning:** Pod starting, virtual target provisioning, exporter registering.
- **Ready:** Exporter registered and available for lease.
- **Leased:** Exporter assigned to an active lease.
- **Terminating:** Instance being deleted (scale-down).

### Component Interaction

1. Administrator creates `VirtualTargetClass` and `ExporterSet` resources.
2. The provisioner controller provisions `minAvailableReplicas` Exporters (each
   owning a `*VirtualTarget`).
3. Each instance Pod boots the virtual target and runs the Jumpstarter exporter,
   registering with the existing `jumpstarter-controller`.
4. Instances appear as regular exporters with labels from `spec.template.metadata`.
5. Users lease them normally — the existing controller handles assignment.
6. On lease release, the instance is recycled per `recycleStrategy`:
   - **Exit-and-replace (default):** Exporter exits; controller replaces the
     instance proactively to maintain `minAvailableReplicas`.
   - **In-place reuse:** Exporter resets internal state without exiting; Pod
     remains running and transitions back to Ready immediately.
7. The `ExporterSet` controller continuously monitors utilization and scales.

### Failure Modes

- **Pod crash:** Controller detects failure via Pod status, replaces the instance,
  maintains `minAvailableReplicas` invariant.
- **Resource exhaustion:** Cannot scale beyond cluster capacity; set stays at
  current size, new leases queue as for physical targets.
- **Provisioner startup failure:** Instance marked failed, controller retries with
  backoff, alerts via conditions on the set status.
- **Scaling storm:** Rate limiting on scale-up prevents creating too many
  instances simultaneously.

## Test Plan

<!-- TODO: Detail specific test cases -->

### Unit Tests
Unit tests should meet the project test coverage requirements.

### Integration Tests

- End-to-end lease lifecycle with QEMU provisioner in a test cluster
- Mixed physical/virtual lease orchestration
- Provisioner failure and recovery scenarios
- `VirtualTargetClass` credential injection and claim binding

## Acceptance Criteria

- [ ] `VirtualTargetClass`, `QEMUVirtualTarget`, and `ExporterSet` CRDs defined
- [ ] `ExporterSet` controller maintains `minAvailableReplicas` warm buffer
- [ ] Controller scales up when available pool is depleted (up to `maxReplicas`)
- [ ] Controller scales down idle replicas after cooldown (never below
      `minAvailableReplicas`)
- [ ] QEMU provisioner (`qemu.jumpstarter.dev`) fully implemented and tested
- [ ] Virtual instances register as standard exporters and are leasable without
      changes to the existing lease flow
- [ ] Pod failures detected and reported in `ExporterSet` status
- [ ] An `ExporterSet` with `minAvailableReplicas: 0` provisions on demand only
- [ ] Status subresource reports Deployment-style counters and health conditions
- [ ] `scale` subresource enables `kubectl scale` interoperability
- [ ] Documentation covers `VirtualTargetClass`, `*VirtualTarget`, and
      `ExporterSet` configuration

## Graduation Criteria

### Experimental

- QEMU provisioner functional in a development cluster
- Basic set lifecycle works end-to-end (scale up, lease, release, scale down)
- Community feedback on CRD schema and scaling behavior

### Stable

- At least two provisioners implemented (e.g., `qemu.jumpstarter.dev` +
  `corellium.jumpstarter.dev`)
- Production usage by at least one team for >1 month
- Performance benchmarks documented (cold-start latency, scaling responsiveness)
- Provisioner authoring guide published (how to add a new provisioner + claim kind)

## Backward Compatibility

- Existing physical-only workflows are unaffected; lease requests without
  virtual-specific labels continue to work as before.
- No changes to the existing gRPC protocol for physical exporters.
- New CRDs (`VirtualTargetClass`, `*VirtualTarget`, `ExporterSet`) are additive.
- **Exporter `enabled` field:** Defaults to `true`, so all existing Exporters
  continue to behave exactly as before.
- Administrators upgrading see no behavior change until they explicitly deploy
  `ExporterSet` and `VirtualTargetClass` resources.

## Consequences

### Positive

- **Instant lease fulfillment:** Warm pools eliminate provisioning latency.
- **Elastic scaling:** Sets grow and shrink with demand.
- **Unified user experience:** Virtual and physical targets leased the same way.
- **Kubernetes-native UX:** `minReplicas`/`maxReplicas`/`minAvailableReplicas`,
  Deployment-style status, `kubectl scale` — familiar to cluster admins.
- **Pluggable backends:** New provisioners add a claim kind + provisioner string.
- **Credential separation:** `VirtualTargetClass` keeps secrets off namespaced claims.
- **Fidelity ladder:** Same lease flow across sim, cloud virtual, and hardware tiers.

### Negative

- **Increased CRD surface:** `VirtualTargetClass`, typed `*VirtualTarget`,
  and `ExporterSet` add more resources to manage than a single pool CRD per provider.
- **Resource consumption:** Warm pools consume cluster resources when idle.
- **Sidecar complexity:** Container-backed provisioners require multi-container
  Pod orchestration and shared-volume protocols.

### Risks

- **Scaling storms:** Burst demand could exhaust cluster resources; rate limiting
  mitigates but may delay lease fulfillment.
- **Provisioner reliability:** Failed startups can cause crash-replace loops.

## Rejected Alternatives

- **Static fixed-size pools (status quo):** Cannot scale with demand.
- **External orchestration (Terraform/Ansible):** Breaks lease semantics integration.
- **Per-lease `parameters` dictionary:** See DD-4.
- **CRD-per-pool without VirtualTarget separation:** Couples scaling and provider
  config; rejected in favor of generic `ExporterSet` + pluggable provisioner.

## Prior Art

- **LAVA:** Virtual DUTs via QEMU with static configuration; no on-demand scaling.
- **Crossplane:** General-purpose cloud composition; no Jumpstarter lease semantics.
  Useful reference for external API integration (e.g., Corellium) but does not
  replace pool-specific scaling logic.
- **CSI (StorageClass/PVC):** Pattern adopted for `VirtualTargetClass`/`*VirtualTarget`.
- **KubeVirt:** VM orchestration with pre-mounted images; Jumpstarter differs by
  flash-at-runtime model and exporter-as-sidecar pattern.

## Unresolved Questions

- What is the exact scaling algorithm (proportional, step-based, predictive)?

### Resolved

- **Observability (JEP-0013):** Provisioner controllers emit metrics per JEP-0013.
- **Lease release detection:** Controllers watch Lease objects directly.
- **Scheduled leases:** `Spec.BeginTime` on Lease CRs; controllers ignore future-dated
  leases until effective.

## Future Possibilities

The following extensions are explicitly **not** part of this JEP but the model
stays open to them:

- **Disaggregated/cross-node accelerators** — ARM64 runtime bridged to a remote
  GPU via virtio-gpu/RDMA.
- **Separate `ProviderConfig` CRD** — multi-account credential reuse and rotation
  referenced by multiple `VirtualTargetClass` resources.
- **Realized-instance CRD (PV analog)** — for static/pre-provisioned devices that
  exist outside the dynamic provisioning flow.
- **`ExporterDeployment` rollout tier** — Deployment analog for rolling updates
  across pool instances (versioned template changes).
- **Multiple/spawned-on-lease VirtualTargets per Exporter** — composite benches
  and multi-device topologies.
- **Universal physical+virtual `Target` abstraction** — single resource type
  spanning hardware and virtual backends.
- **Priority selectors / DeviceClass** — ordered label fallback ("prefer hardware,
  fall back to QEMU") at lease time.
- **HPA/KEDA metric exposure** — complementary external autoscaling once core
  provisioner controllers are stable.
- **Renode provider** — `renode.jumpstarter.dev` provisioner leveraging JEP-0010.
- **Composite leases** — multiple exporters linked into one logical lease.

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

### Phase 2: Core CRDs and QEMU provisioner

Define `VirtualTargetClass`, `QEMUVirtualTarget`, and `ExporterSet` CRDs.
Implement the `qemu.jumpstarter.dev` provisioner with sidecar Pod rendering and
core reconciliation loop.

**Deliverables:**

- [ ] Define `VirtualTargetClass`, `QEMUVirtualTarget`, `ExporterSet` CRD schemas
- [ ] Implement exporter-set controller binary with `--provisioner=qemu.jumpstarter.dev`
- [ ] Sidecar Pod rendering (exporter native sidecar + QEMU runtime container)
- [ ] Core scaling logic: `minAvailableReplicas`, demand-driven scale-up, graceful
      scale-down
- [ ] Deployment-style status + `scale` subresource
- [ ] Watch Leases and Exporters for scaling decisions
- [ ] Add `exporterSets` section to `Jumpstarter` operator CR
- [ ] Integration test: deploy `ExporterSet`, lease, release, observe scaling

### Phase 3: Additional provisioners

Add Corellium and Android provisioners using the same binary with different
`--provisioner` flags.

**Deliverables:**

- [ ] `corellium.jumpstarter.dev` provisioner + `CorelliumVirtualTarget` CRD
- [ ] `android.jumpstarter.dev` provisioner + `AndroidVirtualTarget` CRD
- [ ] Provisioner authoring guide

## Implementation History

- 2025-10-30: RFE filed upstream (GitHub #41)
- 2026-06-03: JEP proposed
- 2026-06-18: Revised per review — ExporterSet, VirtualTargetClass, pluggable
  provisioner model; added end-to-end flow section

## References

- [GitHub Issue #41: RFE: On-Demand Virtual Target Provisioning](https://github.com/jumpstarter-dev/jumpstarter/issues/41)
- [PITCREW-409: jumpstarter JEP: virtual scalable exporters](https://redhat.atlassian.net/browse/PITCREW-409)
- [JEP-0010: Renode Integration](JEP-0010-renode-integration.md) — Related provider
- [JEP-0013: Observability](JEP-0013-observability-telemetry-logs.md) — Integration point

---

This JEP is licensed under the
[Apache License, Version 2.0](https://www.apache.org/licenses/LICENSE-2.0),
