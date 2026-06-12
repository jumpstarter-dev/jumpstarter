# JEP-0014: Virtual Scalable Exporters

| Field             | Value                                                    |
| ----------------- | -------------------------------------------------------- |
| **JEP**           | 0014                                                     |
| **Title**         | Virtual Scalable Exporters                               |
| **Author(s)**     | @mangelajo (Miguel Angel Ajo Pelayo)                     |
| **Status**        | Draft                                                    |
| **Type**          | Standards Track                                          |
| **Created**       | 2026-06-03                                               |
| **Updated**       | 2026-06-12                                               |
| **Discussion**    | https://github.com/jumpstarter-dev/jumpstarter/issues/41 |
| **Requires**      |                                                          |
| **Supersedes**    |                                                          |
| **Superseded-By** |                                                          |

---

## Abstract

This JEP proposes a Virtual Scalable Exporter subsystem for Jumpstarter that
manages pools of virtual targets with configurable autoscaling, modeled directly
on familiar Kubernetes workload primitives. An `ExporterSet` (a ReplicaSet-with-
autoscaling analog) maintains a warm pool of ready-to-lease `Exporter`s; each
`Exporter` is the minimum leased unit and exposes one or more `VirtualTarget`s —
the actual virtual devices (a QEMU VM, an Android emulator, a Corellium device).
A `VirtualTarget` presents a single unified interface backed by a pluggable
scheduling backend (a Kubernetes container, an EC2 instance, a REST API), chosen
through a CSI-style `VirtualTargetClass` (a `StorageClass` analog) that an admin
defines. By keeping a warm buffer of pre-spawned instances the system avoids the
10-60s cold-start latency of VM boot and registration, while scaling up or down
with demand — giving low-latency lease acquisition, massive scalability, and a
unified physical/virtual experience tunable per target type.

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
  `minAvailableReplicas: 2, maxReplicas: 20`, **so that** there are always warm
  instances ready while the system scales up on demand and scales down when idle.

- **As a** cost-conscious operator, **I want to** set `minAvailableReplicas: 0`
  for rarely-used target types, **so that** they consume no resources until
  actually requested, accepting a cold-start delay.

## Proposal

The proposal introduces **Virtual Scalable Exporters** — a set of controllers and
CRDs that manage pools of virtual target instances with configurable autoscaling,
modeled directly on Kubernetes workload primitives so that the system reads like
native Kubernetes to cluster administrators.

The model maps onto a chain admins already understand:

```text
VirtualTargetClass   (cluster-scoped)   ~ StorageClass    — admin-defined "kind of target"
        ▲ virtualTargetClassName
ExporterSet          (generic)          ~ ReplicaSet + HPA — maintains a warm pool
  └ Exporter         (a scheduled Pod)  ~ Pod              — the MINIMUM LEASED UNIT
      └ VirtualTarget(s) (typed claim)  ~ PersistentVolumeClaim — the actual device(s)
            └ realized device                            ~ PersistentVolume
              (k8s container | EC2 instance | Corellium REST API)
```

### Core Concept: Sets, Classes, and Claims

A virtual target pool is an **`ExporterSet`** — a generic, autoscaling controller
analogous to a `ReplicaSet` paired with a Horizontal Pod Autoscaler. It maintains
a configurable number of warm `Exporter`s from an inline template, scaling between
`minReplicas` and `maxReplicas` and keeping `minAvailableReplicas` ready-and-
unleased at all times.

Each `Exporter` is the **minimum leased unit** (unchanged from today — leases bind
to `Exporter`s) and always runs in a scheduled Pod. An `Exporter` exposes one or
more **`VirtualTarget`s**: the actual virtual devices. A `VirtualTarget` is the
single point of provider-specific typing — it presents a uniform interface but is
backed by a **pluggable scheduling backend** chosen by its kind, analogous to how
CSI backs a `PersistentVolumeClaim`, CRI backs a Pod, or a Cluster API
infrastructure provider backs a `Machine`:

- `QEMUVirtualTarget` → schedules a Kubernetes container (plus an OS image
  mounted as an OCI artifact)
- `AndroidVirtualTarget` → schedules an Android Cuttlefish container
- `EC2VirtualTarget` → provisions an instance via the AWS API
- `CorelliumVirtualTarget` → provisions a device via the Corellium REST API

Following the CSI model, a cluster-scoped **`VirtualTargetClass`** (a
`StorageClass` analog) names a `provisioner`, holds credentials and provisioning
settings, and sets policy (`bindingMode`, `reclaimPolicy`, node scheduling). The
typed `VirtualTarget` is the **claim** (a `PersistentVolumeClaim` analog): it names
a class via `virtualTargetClassName` and carries only API-server-validated
per-device fields. Admins own classes and their credentials; users — and the
`ExporterSet` template — simply name a class.

#### Example: a QEMU (container-backed) pool

```yaml
apiVersion: jumpstarter.dev/v1alpha1
kind: ExporterSet                  # generic; autoscaling (ReplicaSet + HPA)
metadata:
  name: rpi4-virtual
  namespace: jumpstarter
spec:
  minReplicas: 0                   # HPA-style floor
  maxReplicas: 20                  # HPA-style ceiling
  minAvailableReplicas: 2          # PDB-style warm buffer (ready & unleased)
  recycleStrategy: ExitAndReplace  # ExitAndReplace (default) | InPlaceReuse
  selector:
    matchLabels:
      board: rpi4
  template:                        # Exporter template (the leasable Pod)
    metadata:
      labels:
        board: rpi4
        arch: aarch64
        virtual: "true"
    spec:
      exporterImage: quay.io/jumpstarter-dev/exporter-qemu:latest
      drivers:                     # in-process Python drivers; attach to a target by name
        - type: jumpstarter_driver_power.driver.QemuPower
          config: { target: main }
        - type: jumpstarter_driver_serial.driver.QemuSerial
          config: { target: main }
      virtualTargets:              # provider-typed claim(s); the KIND selects the backend
        - kind: QEMUVirtualTarget  # → schedules a k8s container
          name: main
          spec:
            virtualTargetClassName: qemu-rpi4   # class carries provisioner + scheduling
            machineType: virt
            cpu: 4
            memory: 4Gi
            runtimeImage: quay.io/jumpstarter-dev/qemu-system:9.0          # standalone QEMU build
            osImage: { reference: registry.example.com/os/rpi4:latest }    # OS as an OCI artifact
```

The matching class declares the compute contract (ARM64 + KVM nodes), so the
exporter Pod lands on the right hardware:

```yaml
apiVersion: jumpstarter.dev/v1alpha1
kind: VirtualTargetClass           # cluster-scoped; StorageClass analog
metadata:
  name: qemu-rpi4
spec:
  provisioner: qemu.jumpstarter.dev
  bindingMode: Immediate           # pre-warmed pool (WaitForFirstConsumer = provision-on-lease)
  reclaimPolicy: Delete
  scheduling:                      # inherited by the rendered exporter Pod
    nodeSelector:
      kubernetes.io/arch: arm64
    tolerations:
      - { key: jumpstarter.dev/kvm, operator: Exists, effect: NoSchedule }
    resources:
      limits:
        devices.kubevirt.io/kvm: "1"   # KVM via device plugin (or privileged + /dev/kvm)
  # a GPU-backed class would add nvidia.com/gpu limits + the GPU node toleration
```

#### Example: a Corellium (API-backed) pool

The same `ExporterSet`/`Exporter` shape is used; only the `VirtualTarget` kind
differs, and there is no local runtime container — the device is provisioned via a
remote API. Credentials and account settings live **on the class** (an inline
`credentialsSecretRef` plus opaque `parameters`, exactly as a `StorageClass` names
its provisioner secrets); the typed claim just names the class and carries
per-device fields.

```yaml
apiVersion: jumpstarter.dev/v1alpha1
kind: VirtualTargetClass
metadata:
  name: corellium-kronos
spec:
  provisioner: corellium.jumpstarter.dev
  credentialsSecretRef:            # like a CSI provisioner secret
    name: corellium-creds
    namespace: jumpstarter
  parameters:                      # opaque account/provisioning settings, provisioner-validated
    apiHost: app.corellium.com
    projectId: "778f00af-5e9b-40e6-8e7f-c4f14b632e9c"
  bindingMode: WaitForFirstConsumer  # provision-on-lease (Immediate = pre-warmed)
  reclaimPolicy: Delete
---
# In the ExporterSet's Exporter template — the typed claim names the class:
      virtualTargets:
        - kind: CorelliumVirtualTarget   # → provisions via the Corellium REST API
          name: main
          spec:
            virtualTargetClassName: corellium-kronos   # like storageClassName
            deviceFlavor: kronos                        # per-device, API-server validated
# (an EC2VirtualTarget names an EC2-provisioner class whose parameters hold
#  region/VPC and whose credentialsSecretRef holds the AWS creds; the claim
#  carries instanceType/ami per device.)
```

An `ExporterSet` with `minAvailableReplicas: 0` consumes no resources until a
lease is requested, accepting cold-start latency. One with `minAvailableReplicas: 3`
always keeps 3 ready-to-lease instances — leases are fulfilled instantly from the
warm pool, and the controller scales up if more are needed. For expensive
API-backed classes, `bindingMode: WaitForFirstConsumer` keeps the claim/slot
reserved while deferring the costly device provisioning until the lease is actually
acquired.

### Exporter and VirtualTargets

The split between the `Exporter` (the access/control plane that runs drivers and
is leased) and the `VirtualTarget` (the provider-typed device) is what makes the
model both Kubernetes-native and extensible. The `Exporter` always runs in a
scheduled Pod and attaches to its `VirtualTarget`(s) either **locally** (a
co-located runtime container over a shared volume) or **remotely** (a network/API
endpoint), depending on the target type.

**Container-backed targets** (`QEMUVirtualTarget`, `AndroidVirtualTarget`) render
an instance Pod composed of three independently shipped artifacts — the exporter
container (drivers delivered as image layers), the target-runtime container (the
QEMU/Cuttlefish build, its own image), and the OS image (an OCI artifact mounted
via a Kubernetes image volume). The exporter runs as a **native sidecar** so it
starts first, registers the `Exporter`, and outlives the workload during a drain:

```yaml
# Pod rendered by the qemu provisioner for one Exporter instance
spec:
  initContainers:
    - name: exporter                # native sidecar: starts first, outlives workload for drain
      image: quay.io/jumpstarter-dev/exporter-qemu:latest
      restartPolicy: Always
      volumeMounts:
        - { name: ipc, mountPath: /run/jmp }
  containers:
    - name: target-main             # the QEMUVirtualTarget runtime (independent image)
      image: quay.io/jumpstarter-dev/qemu-system:9.0
      volumeMounts:
        - { name: os,  mountPath: /os }        # OS delivered as an OCI artifact
        - { name: ipc, mountPath: /run/jmp }   # QMP/serial shared with the exporter
  volumes:
    - name: os
      image:
        reference: registry.example.com/os/rpi4:latest   # image volume (Kubernetes 1.31+, GA 1.33)
    - name: ipc
      emptyDir: {}
```

**API-backed targets** (`CorelliumVirtualTarget`, `EC2VirtualTarget`) have no
local runtime container; the provisioner creates the device via API and the
exporter's driver connects out over the network. The exporter still always runs in
a scheduled Pod.

Because the exporter is isolated from the runtime, it survives target failures and
can drain gracefully. The model also accommodates an `Exporter` owning multiple
`VirtualTarget`s (multi-device benches, spawned on lease) — see *Future
Possibilities* — without changing the lease semantics.

### Exporter ↔ VirtualTarget Interface (Standard Interfaces; virtio)

A primary reason the split is worth it: the exporter's drivers consume the **same
standard interfaces** for virtual targets as for physical ones — Serial, SPI, I2C,
CAN, GPIO, network, storage. A driver opens a character device or socket and does
not care whether the other end is real hardware or a virtual target, so **driver
code is reused unchanged across physical and virtual**.

For VM/container-backed targets, **virtio** is the recommended transport: the guest
exposes virtio devices and the host-side backends surface them to the exporter as
standard endpoints over the shared volume:

- `virtio-console` / `virtio-serial` → PTY/Unix socket → Serial driver
- `virtio-vsock` (AF_VSOCK) → network-style drivers (already used today for SSH via
  `VsockNetwork(cid, port=22)` in the QEMU driver)
- `virtio-net` → TcpNetwork; `virtio-blk` / `virtio-scsi` → storage/flashing
- `virtio-gpio` / `virtio-i2c` / `virtio-can` → GPIO/I2C/CAN (newer virtio device
  types; guest/host coverage is still maturing)

**Privilege:** virtio with socket/chardev/vsock backends lets the exporter attach
over Unix sockets/vsock instead of passing through host kernel devices, which
**avoids privileged containers** for many interfaces. Exact kernel-interface
fidelity (real SocketCAN via `vcan` + `NET_ADMIN`, `/dev/spidev` passthrough)
remains the privileged, high-fidelity fallback — virtio is the privilege-reducing
default, not a universal drop-in.

This applies to VM/container-backed targets only; API-backed targets use native
network/API transports. Transport is therefore part of each `VirtualTarget` type's
contract, not universal.

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

```text
                       ┌─────────────────────────┐
                       │  jumpstarter-controller │
                       │  (creates Leases,       │
                       │   assigns Exporters)    │
                       └──────────┬──────────────┘
                                  │ assigns Exporters to Leases
                                  ▼
                 ┌────────────────────────────────────────────┐
                 │               Kubernetes API               │
                 │  Lease / Exporter / ExporterSet /          │
                 │  VirtualTargetClass / *VirtualTarget CRs   │
                 └─┬───────────────────────┬──────────────────┘
                   │ watches Leases +      │ watches *VirtualTarget claims
                   │ Exporters             │ whose class names this provisioner
                   ▼                       ▼
        ┌────────────────────┐  ┌─────────────────────────────────────────┐
        │ ExporterSet        │  │ Provisioners (one Deployment per backend) │
        │ controller         │  │  qemu / android / ec2 / corellium ...    │
        │ (generic, autoscale)│ └───────────────────┬─────────────────────┘
        └─────────┬──────────┘                      │ realize device on backend
                  │ creates Exporters (Pods)        ▼
                  │ from inline template     container | EC2 API | Corellium API
                  ▼
        ┌───────────────────────────────────┐
        │ Warm pool of Exporters (Pods)     │
        │ [exp1][exp2]..  each owns         │
        │ VirtualTarget(s) + drivers        │
        └───────────────────────────────────┘
                  │ register as standard Exporter CRs
                  ▼
            Kubernetes API (Exporters → leasable)
```

**Scaling inputs.** The `ExporterSet` controller watches two resources to drive
scaling decisions:

1. **Leases** — pending Leases whose label selectors match the set's
   `template.metadata.labels` signal demand and trigger scale-up.
2. **Exporters** — the Exporters it owns, to track which are available (no active
   lease) vs. leased, and thus current utilization.

If there are pending leases this set could serve and no available instances, it
scales up; if excess idle instances persist beyond `minAvailableReplicas` for a
cooldown, it scales down.

**Controllers.** The generic `ExporterSet` controller is provider-agnostic and
always on. Each backend ships a **provisioner** reconciler that watches the typed
`*VirtualTarget` claims whose class names its `provisioner` and realizes the
device on its backend. All provisioners are compiled into a single binary; each
Deployment passes a `--provider=<name>` flag, giving isolated logs and independent
restarts from one image. The Jumpstarter operator deploys these based on the
`Jumpstarter` CR (see below).

**Instance lifecycle.**

1. The `ExporterSet` controller creates an Exporter (Pod) from `spec.template`,
   including its `VirtualTarget` claim(s).
2. The class's provisioner realizes each `VirtualTarget` on its backend (a
   container, an EC2 instance, a Corellium device). The exporter sidecar attaches
   to it and registers as a standard Exporter.
3. The instance becomes available in the warm pool for lease assignment.
4. On lease release, the instance is recycled per `recycleStrategy`; the set keeps
   `minAvailableReplicas` warm.

### API / Protocol Changes

**New CRD: `ExporterSet` (generic)**

```yaml
spec:
  # Scaling (HPA / PodDisruptionBudget vocabulary)
  minReplicas: <int>            # floor: total instances never below this (default: 0)
  maxReplicas: <int>           # ceiling: never exceed (0 or omitted = no limit)
  minAvailableReplicas: <int>  # warm buffer: keep N ready-and-unleased (PDB minAvailable, default: 0)
  scaleDownCooldown: <duration>  # wait before scaling down (default: 5m)
  recycleStrategy: <string>    # "ExitAndReplace" (default) or "InPlaceReuse"

  # Selector + inline template (Deployment/ReplicaSet idiom)
  selector:
    matchLabels: { <key>: <value> }
  template:
    metadata:
      labels: { <key>: <value> }   # must be a superset of selector.matchLabels
    spec:
      exporterImage: <string>      # exporter container image (drivers as layers)
      drivers:
        - type: <driver-class>
          config: { target: <name>, ... }
      virtualTargets:              # 1..N provider-typed claims
        - kind: <ProviderVirtualTarget>
          name: <name>
          spec:
            virtualTargetClassName: <class>
            # provider-typed, API-server-validated per-device fields
```

The CRD enables the Kubernetes **`scale` subresource**
(`specReplicasPath: .spec.maxReplicas`, `statusReplicasPath: .status.replicas`,
`labelSelectorPath: .status.selector`) so `kubectl scale` works and HPA/KEDA can
drive the ceiling, while the built-in controller scales within
`[minReplicas, maxReplicas]` using lease-aware logic.

**Status subresource:**

```yaml
status:
  replicas: 5            # total instances
  readyReplicas: 3       # registered & ready
  availableReplicas: 3   # ready AND unleased — the warm buffer
  leasedReplicas: 2      # ready and currently leased
  selector: "board=rpi4,virtual=true"   # required by the scale subresource
  conditions:
    - { type: Available,      status: "True" }
    - { type: ScalingLimited, status: "False" }
```

**New CRD: `VirtualTargetClass` (cluster-scoped; StorageClass analog)**

```yaml
spec:
  provisioner: <string>            # e.g. qemu.jumpstarter.dev (selects the backend controller)
  credentialsSecretRef:            # inline, like a CSI provisioner secret (omit for local backends)
    name: <string>
    namespace: <string>
  parameters: { <key>: <value> }   # opaque account/provisioning settings, provisioner-validated
  bindingMode: <string>            # Immediate (pre-warmed) | WaitForFirstConsumer (provision-on-lease)
  reclaimPolicy: <string>          # Delete (default) | Retain
  scheduling:                      # inherited by the rendered exporter Pod
    nodeSelector: { <key>: <value> }
    nodeAffinity: { ... }
    tolerations: [ ... ]
    resources:                     # device requests (KVM, GPU, etc.)
      limits: { <resource>: <quantity> }
```

A default class is marked with the `jumpstarter.dev/is-default-class: "true"`
annotation (StorageClass precedent).

**New CRDs: `*VirtualTarget` (provider-typed; PVC analog / the claim)**

Each backend defines a strongly-typed claim that names a class and carries
per-device fields. The kind determines the scheduling backend:

- `QEMUVirtualTarget`: `machineType`, `cpu`, `memory`, `runtimeImage`, `osImage`, …
- `AndroidVirtualTarget`: `systemImage`, `avdProfile`, `runtimeImage`, …
- `CorelliumVirtualTarget`: `deviceFlavor`, `deviceOs`, … (settings via the class)
- `EC2VirtualTarget`: `instanceType`, `ami`, … (region/VPC/credentials via the class)

**Changes to existing CRDs — `Exporter` gains `spec.enabled`:**

```yaml
apiVersion: jumpstarter.dev/v1alpha1
kind: Exporter
metadata:
  name: qemu-rpi4-instance-3
spec:
  enabled: false  # controller will not assign new leases to this exporter
```

`enabled` defaults to `true`. When `false`, `jumpstarter-controller` will not
assign new leases — analogous to cordoning a Node (`spec.unschedulable`). This is
used for lab maintenance and for the graceful scale-down "drain" sequence (cordon →
confirm no lease assigned → delete). The `Exporter` also references its
`VirtualTarget`(s).

**Operator CR (`Jumpstarter`) — new `exporterControllers` section:**

```yaml
apiVersion: operator.jumpstarter.dev/v1alpha1
kind: Jumpstarter
metadata:
  name: jumpstarter
  namespace: jumpstarter
spec:
  # ... existing controller, routers, authentication config ...

  exporterControllers:
    # Default image shared by all controllers (can be overridden per provisioner)
    image: quay.io/jumpstarter-dev/pool-controller:latest
    imagePullPolicy: IfNotPresent

    # The generic ExporterSet controller is always on.
    # Each provisioner reconciles the *VirtualTarget claims whose class names it.
    provisioners:
      - name: qemu
        enabled: true
        resources:
          requests: { cpu: 100m, memory: 256Mi }
      - name: corellium
        enabled: false
        image: quay.io/jumpstarter-dev/pool-controller-corellium:latest
        imagePullPolicy: Always
```

The operator creates one Deployment per enabled provisioner (passing
`--provider=<name>`), and handles RBAC, service accounts, and lifecycle.

### Hardware Considerations

This proposal is specifically designed to reduce reliance on physical hardware
for scalable testing. However:

- Virtual targets must faithfully emulate the interfaces exposed by physical
  hardware (serial, network, storage, power) through the existing driver model —
  see *Exporter ↔ VirtualTarget Interface* above.
- Node scheduling is folded into the `VirtualTargetClass` (`scheduling` block):
  `nodeSelector`/`nodeAffinity` for architecture (`kubernetes.io/arch: arm64`),
  `tolerations` for tainted KVM/GPU/baremetal nodes, and device resource requests.
  QEMU/Renode require `/dev/kvm` for acceptable performance — exposed via a device
  plugin (`devices.kubevirt.io/kvm`) or a privileged container — and KVM cannot
  accelerate cross-architecture, so arch must match. These constraints matter for
  container-backed classes; API-backed classes keep the exporter Pod light.
- Timing-sensitive tests (USB/IP latency, boot ROM timeouts) may behave
  differently on virtual targets — the system exposes labels indicating whether a
  target is physical or virtual so users can filter when fidelity matters.

## Design Decisions

### DD-1: Pool-based scaling vs. purely on-demand provisioning

**Alternatives considered:**

1. **Pool-based with configurable min/max** — Maintain a warm pool of
   pre-spawned instances; scale between `minReplicas`/`minAvailableReplicas` and
   `maxReplicas`.
2. **Purely on-demand** — Spawn a new instance only when a lease request arrives;
   destroy it when the lease is released.

**Decision:** Pool-based with configurable min/max.

**Rationale:** Purely on-demand provisioning introduces noticeable latency for
CI pipelines (Pod scheduling + image pull + VM boot + exporter registration
typically takes 10-15s, and up to 60s with cold image pulls or heavy providers).
A warm pool provides instant lease fulfillment for the common case. Setting
`minAvailableReplicas: 0` (optionally with `bindingMode: WaitForFirstConsumer`)
still allows purely on-demand behavior for rarely-used targets, giving operators
full control over the trade-off.

### DD-2: Controller deployment model

**Alternatives considered:**

1. **Separate binary per provider** — Each backend is a completely independent
   binary/image.
2. **Single binary, one Deployment per provisioner** — One image contains the
   generic `ExporterSet` controller and all provisioner reconcilers; a CLI flag
   (`--provider=qemu`) selects which provisioner a Deployment activates.
3. **Single binary, single Deployment** — One Deployment runs everything together.
4. **Integrated into jumpstarter-controller** — Add the reconcilers directly into
   the existing operator.

**Decision:** Option 2 — single binary, generic `ExporterSet` controller plus one
Deployment per provisioner.

**Rationale:** A single image is cheaper to build, test, and release. The generic
`ExporterSet` controller is provider-agnostic; the per-provisioner Deployments give
isolated logs and independent restarts. Adding a new backend means a new
Deployment manifest pointing at the same image with a different flag — no new image
build required.

### DD-3: Typed VirtualTarget claims vs. a single generic claim

**Alternatives considered:**

1. **Per-provider typed `*VirtualTarget` claims** — `QEMUVirtualTarget`,
   `CorelliumVirtualTarget`, etc., with API-server-validated schemas.
2. **Single generic `VirtualTarget`** with a `provider.type` field and opaque
   `config` map.

**Decision:** Per-provider typed claims; the orchestration tiers (`ExporterSet`)
and the `VirtualTargetClass` are generic.

**Rationale:** Provider typing belongs on the device claim, where the
configuration actually differs (a QEMU machine type vs. a Corellium device flavor).
Typed claims give IDE completion, webhook validation, and clear per-provider docs.
Keeping the surrounding tiers generic avoids duplicating scaling/lifecycle logic
per provider. New backends add a claim kind + a provisioner without touching the
pool tier. (The CSI-style class itself stays generic — see DD-12.)

### DD-4: Per-lease parameters vs. pool/class flavors

**Alternatives considered:**

1. **Per-lease `parameters` dictionary** — Leases carry an opaque map that
   provisioners interpret when provisioning instances.
2. **Multiple flavors via separate sets/classes** — Administrators create separate
   `ExporterSet`s and `VirtualTargetClass`es for different resource profiles; users
   select via label matching at lease time.

**Decision:** Option 2 — multiple flavors via separate sets/classes.

**Rationale:** Per-lease parameters add complexity across every layer (Lease CRD,
controller pass-through, provisioner parsing, driver template overrides). The same
need is met by distinct `ExporterSet`s (different labels) and distinct
`VirtualTargetClass`es (different resource profiles), keeping the Lease API
unchanged. Per-lease parameters can be revisited in a future JEP if this proves
insufficient.

### DD-5: Built-in scaling vs. HPA / KEDA

**Alternatives considered:**

1. **Built-in scaling logic** in the `ExporterSet` controller.
2. **Kubernetes HPA** with custom metrics.
3. **KEDA** with a custom scaler.

**Decision:** Built-in scaling logic as the primary autoscaler, **and** expose the
`scale` subresource so HPA/KEDA can complement it.

**Rationale:** The controller needs Jumpstarter-specific knowledge generic
autoscalers cannot express: label matching between pending Leases and pool labels,
the graceful disable-before-delete drain, awareness of exporter readiness, and the
`minAvailableReplicas` warm-buffer invariant. By exposing the standard `scale`
subresource (with `specReplicasPath: .spec.maxReplicas`), HPA or KEDA can still
drive the **ceiling** as a complementary input while the built-in controller owns
warm-pool maintenance and lease-aware matching within `[minReplicas, maxReplicas]`.

### DD-6: Align with Kubernetes workload primitives

**Alternatives considered:**

1. **A bespoke pool schema** (the earlier `*ExporterPool` draft) with a flat
   `labels:` field, custom scaling vocabulary, and no `scale` subresource.
2. **Adopt the Deployment/ReplicaSet/CSI idioms** — `selector` + inline `template`,
   HPA/PDB scaling vocabulary, the `scale` subresource, Deployment-style status,
   `ownerReferences`-based GC, and a cordon/drain analogy for `enabled`.

**Decision:** Option 2.

**Rationale:** Kubernetes-native admins should be able to read the system without a
new mental model. The mapping is direct:

| Kubernetes primitive               | JEP-0014 concept                                       |
| ---------------------------------- | ------------------------------------------------------ |
| ReplicaSet + HPA                   | generic `ExporterSet`                                  |
| Pod                                | `Exporter` (minimum leased unit)                       |
| StorageClass                       | `VirtualTargetClass`                                   |
| PersistentVolumeClaim              | typed `*VirtualTarget` (the claim)                     |
| PersistentVolume                   | the realized device                                    |
| CSI / CRI / CAPI infra provider    | the `VirtualTarget` provisioner backend                |
| `volumeBindingMode`                | `bindingMode` (warm vs. provision-on-lease)            |
| `reclaimPolicy` / default class    | `reclaimPolicy` / default `VirtualTargetClass`         |
| PodDisruptionBudget `minAvailable` | `minAvailableReplicas`                                 |
| `scale` subresource                | `ExporterSet` scaling (HPA/KEDA may drive the ceiling) |
| native sidecar / image volume      | exporter sidecar / OS OCI artifact                     |
| Node cordon + drain                | `Exporter.spec.enabled: false` + graceful scale-down   |

This also reuses the existing lease-selector → Exporter-label matching unchanged.

### DD-7: VirtualTarget as a unified interface over pluggable scheduling backends

**Alternatives considered:**

1. **One CRD per scheduler with no shared interface.**
2. **Typed `*VirtualTarget` kinds sharing one interface, each with a backend
   controller** (container / AWS / Corellium).
3. **An opaque generic target** with no contract.

**Decision:** Option 2.

**Rationale:** Virtual targets are scheduled in fundamentally different ways — a
Kubernetes container, an EC2 instance, a Corellium device via REST — yet the
exporter and lease experience should be identical regardless. A unified
`VirtualTarget` interface with a pluggable backend (the CSI/CRI/CAPI-provider
pattern) achieves this: new backends add a kind + provisioner without touching the
pool tier, and the `Exporter` attaches over standard interfaces either locally or
remotely.

### DD-8: Exporter remains the leased unit; VirtualTarget is separate

**Alternatives considered:**

1. **Exporter conflates device and access** (status quo — one exporter ≈ one
   device).
2. **Separate a generic, leasable `Exporter` (always a Pod) owning provider-typed
   `*VirtualTarget`(s).**
3. **Make `VirtualTarget` itself the leasable unit.**

**Decision:** Option 2.

**Rationale:** Keeping `Exporter` as the minimum leased unit leaves the lease flow,
policies, and physical-target behavior unchanged and unified. Separating the
device into `*VirtualTarget` localizes provider typing and enables a future where
one `Exporter` owns several `VirtualTarget`s (multi-device benches, spawned on
lease) without changing lease semantics. The exporter always runs in a scheduled
Pod and attaches locally or remotely.

### DD-9: Single autoscaling tier now; rollout tier and templates deferred

**Alternatives considered:**

1. **A full Cluster-API-style hierarchy** — `ExporterDeployment` (rollout) →
   `ExporterSet` → … plus referenced `*VirtualTargetTemplate` blueprints.
2. **A single autoscaling `ExporterSet` with inline templates.**

**Decision:** Option 2.

**Rationale:** Inline templates are the idiomatic Kubernetes shape — Deployment,
ReplicaSet, Job, DaemonSet, and StatefulSet all embed their template inline;
referenced `*Template` CRDs are a Cluster API exception driven by immutability and
reuse needs this proposal does not yet have. A `Deployment`-equivalent rollout tier
and referenced templates are valuable later but premature now; they are recorded in
*Future Possibilities* and the model leaves room for them.

### DD-10: Exporter ↔ VirtualTarget transport via standard interfaces (virtio)

**Alternatives considered:**

1. **Bespoke RPC** between exporter and target.
2. **Standard interfaces** (serial/SPI/I2C/CAN/GPIO/net/storage) over virtio plus
   host-side endpoints, so existing drivers are reused unchanged.
3. **Pass-through of host kernel devices** (privileged).

**Decision:** Option 2 as the recommended default for VM/container targets, with
Option 3 as the high-fidelity privileged fallback; transport is defined per
`*VirtualTarget` type.

**Rationale:** Drivers should not know whether they talk to physical hardware or a
virtual target. virtio exposes standard endpoints (PTY/socket/vsock) the existing
drivers already understand, reuses mature device types (serial/net/vsock/blk), and
reduces the need for privileged containers. Emerging device types (gpio/i2c/can)
and exact kernel fidelity are handled by the privileged fallback when needed.

### DD-11: Credentials inline on the VirtualTargetClass

**Alternatives considered:**

1. **Embed credentials in each `*VirtualTarget` claim.**
2. **Inline `credentialsSecretRef` + opaque provisioning `parameters` on the
   `VirtualTargetClass`** (the CSI StorageClass provisioner-secret pattern).
3. **A separate `*ProviderConfig` CRD** referenced by the class (Crossplane style).

**Decision:** Option 2.

**Rationale:** This is the lowest-CRD, most CSI-faithful option — a `StorageClass`
references its provisioner secrets directly. Admins own classes and their Secrets;
claim authors never see credentials; many classes can share one Secret.
Container-backed classes need no secret at all. A separately rotatable,
multi-account `*ProviderConfig` object (Option 3) is deferred to *Future
Possibilities* for when that reuse genuinely warrants the extra object.

### DD-12: CSI-style class + claim split (typed claims)

**Alternatives considered:**

1. **Claim only** (no class).
2. **`VirtualTargetClass` (StorageClass analog) + typed `*VirtualTarget` claim
   (PVC analog) + dynamic provisioning.**
3. **Fully generic class + claim** with opaque parameters (pure CSI).

**Decision:** Option 2 — adopt the class/claim structure, dynamic provisioning,
`bindingMode`, `reclaimPolicy`, and default-class, while keeping per-provider
**typed** claims for API-server schema validation.

**Rationale:** The class/claim split gives full Kubernetes consistency and a clean
admin/user separation (admins own classes + credentials; users name a class)
without losing typed validation. The `provisioner` string selects the backend
controller; the class carries the inline `credentialsSecretRef` + `parameters`. The
`bindingMode` knob maps cleanly onto warm-vs-on-lease provisioning, and
`reclaimPolicy` onto recycle behavior.

### DD-13: Node scheduling on the class (with template override)

**Alternatives considered:**

1. **Scheduling only on the `ExporterSet`/Exporter template.**
2. **Scheduling defaults on the `VirtualTargetClass`, overridable by the
   `ExporterSet` template.**
3. **Implicit/automatic placement.**

**Decision:** Option 2.

**Rationale:** The compute requirement (ARM64, GPU, KVM) is intrinsic to a class's
backend, so it belongs with the provisioner definition — mirroring
`StorageClass.allowedTopologies`. The class declares `nodeSelector`/`nodeAffinity`,
`tolerations` for tainted nodes, and device resource requests
(`kubernetes.io/arch`, `devices.kubevirt.io/kvm`, `nvidia.com/gpu`); the rendered
exporter Pod inherits them, and the `ExporterSet` template may override or augment.

## Design Details

### Reconciliation Loops

**`ExporterSet` controller (generic):**

```text
for each ExporterSet:
  ownedExporters   = list Exporters owned by this set
  replicas         = count ownedExporters
  readyReplicas    = count ownedExporters in Ready state
  leasedReplicas   = count readyReplicas with an active LeaseRef
  availableReplicas = readyReplicas - leasedReplicas
  pendingLeases    = count pending Leases whose labels match template labels

  # Invariant: maintain the warm buffer
  if availableReplicas < spec.minAvailableReplicas AND replicas < spec.maxReplicas:
    scale up (restore the warm buffer)

  # Floor
  elif replicas < spec.minReplicas:
    scale up to spec.minReplicas

  # Demand-driven scale-up
  elif pendingLeases > 0 AND replicas < spec.maxReplicas:
    scale up by min(pendingLeases, spec.maxReplicas - replicas)

  # Scale-down: excess idle instances beyond the buffer
  elif availableReplicas > spec.minAvailableReplicas AND cooldown elapsed:
    graceful scale down (never below the buffer / minReplicas):
      1. set exporter.spec.enabled = false        # cordon
      2. wait until confirmed no lease was assigned (leaseRef remains empty)  # drain
      3. delete the Exporter (and its VirtualTarget(s) per reclaimPolicy)
```

**Provisioner reconciler (per backend):**

```text
for each *VirtualTarget claim whose class.provisioner == this provisioner:
  class = get VirtualTargetClass(claim.spec.virtualTargetClassName)
  creds = resolve class.credentialsSecretRef (if any)
  if class.bindingMode == WaitForFirstConsumer AND not leased:
    leave unprovisioned (reserve the slot only)
  else:
    realize the device on the backend (container / EC2 API / Corellium API)
    apply class.scheduling to the rendered exporter Pod
    report Ready when the exporter attaches and registers
```

### Instance States

```text
Provisioning → Ready (warm pool) → Leased → Ready
                                              └→ Terminating → (deleted if availableReplicas > buffer)
```

- **Provisioning:** the device is being realized on its backend; the exporter is
  starting and registering.
- **Ready:** registered and available for lease (counts toward `availableReplicas`).
- **Leased:** assigned to an active lease.
- **Terminating:** being drained and deleted (scale-down), honoring `reclaimPolicy`.

### Recycling

On lease release, two strategies are supported (`recycleStrategy`):

- **ExitAndReplace (default):** the exporter exits after cleanup; the set creates a
  fresh replacement, guaranteeing clean state between leases. Cold-start is absorbed
  by the warm pool.
- **InPlaceReuse:** the exporter resets the device internally (e.g., power off the
  VM, reset state) without exiting, returning to Ready immediately. Useful when
  cold-start is expensive and the backend guarantees clean state after reset.

### Failure Modes

- **Pod crash:** detected via Pod status; the set replaces the instance and
  maintains the warm buffer.
- **Resource exhaustion:** cannot scale beyond cluster/backend capacity; the set
  stays at current size and new leases queue as they would for physical targets.
- **Provisioner/startup failure:** the `VirtualTarget` is marked failed; the
  provisioner retries with backoff and surfaces conditions on the claim and set.
- **Scaling storm:** rate limiting on scale-up prevents creating too many instances
  simultaneously.

## Test Plan

### Unit Tests

- `ExporterSet` scaling logic: warm-buffer invariant, demand-driven scale-up,
  cooldown scale-down, never below `minReplicas`/`minAvailableReplicas`, ceiling at
  `maxReplicas`.
- `scale` subresource read/write and interaction with the built-in controller.
- Graceful drain sequence (`enabled: false` → confirm no lease → delete).
- `VirtualTargetClass` resolution: provisioner selection, `credentialsSecretRef`,
  `bindingMode`, default-class selection.
- Lease assignment skips `enabled: false` exporters.

Unit tests should meet the project test coverage requirements.

### Integration Tests

- End-to-end lease lifecycle with the `qemu` provisioner in a test cluster
  (create `ExporterSet` + `VirtualTargetClass`, warm pool comes up, lease, release,
  observe scale behavior).
- `bindingMode: WaitForFirstConsumer` provisions only on lease.
- Mixed physical/virtual lease orchestration.
- Provisioner failure and recovery scenarios.
- Node scheduling: a class requiring KVM/arm64 lands instances on matching nodes.

## Acceptance Criteria

- [ ] `ExporterSet` CRD is defined and validated by the operator, with the `scale`
      subresource enabled.
- [ ] `VirtualTargetClass` CRD is defined, including `bindingMode`, `reclaimPolicy`,
      `credentialsSecretRef`, `parameters`, `scheduling`, and default-class handling.
- [ ] `QEMUVirtualTarget` (container backend) claim is defined and validated.
- [ ] The `ExporterSet` controller maintains `minAvailableReplicas` warm instances
      and scales up when the warm pool is depleted (up to `maxReplicas`).
- [ ] The controller scales down idle instances after cooldown via the graceful
      drain (never below the warm buffer / `minReplicas`).
- [ ] The `qemu.jumpstarter.dev` provisioner realizes container-backed instances
      that register as standard Exporters and are leasable without changes to the
      existing lease flow.
- [ ] Pod failures are detected and reported in `ExporterSet` and `VirtualTarget`
      status.
- [ ] An `ExporterSet` with `minAvailableReplicas: 0` provisions instances only on
      demand.
- [ ] `ExporterSet` status reports `replicas`/`readyReplicas`/`availableReplicas`/
      `leasedReplicas` and health conditions.
- [ ] `Exporter.spec.enabled` is honored by `jumpstarter-controller`.
- [ ] Documentation covers `ExporterSet`, `VirtualTargetClass`, and provisioner
      setup.

## Graduation Criteria

### Experimental

- `qemu.jumpstarter.dev` provisioner functional in a development cluster.
- Basic pool lifecycle works end-to-end (scale up, lease, release, scale down).
- Community feedback on the CRD schemas and scaling behavior.

### Stable

- At least two provisioners implemented (e.g., `qemu` + `android`).
- Production usage by at least one team for >1 month.
- Performance benchmarks documented (cold-start latency, scaling responsiveness).
- Provisioner authoring guide published (how to add a new `*VirtualTarget` +
  provisioner).

## Backward Compatibility

- Existing physical-only workflows are unaffected; lease requests without
  virtual-specific labels continue to work as before.
- No changes to the existing gRPC protocol for physical exporters.
- The new `ExporterSet`, `VirtualTargetClass`, and `*VirtualTarget` CRDs are
  additive.
- **Exporter `enabled` field:** defaults to `true`, so all existing Exporters
  behave exactly as before. `jumpstarter-controller` must be updated to respect it
  (skip disabled exporters during lease assignment).
- Administrators upgrading to a pool-enabled version see no behavior change until
  they explicitly deploy an `ExporterSet` and a `VirtualTargetClass`.

## Consequences

### Positive

- **Instant lease fulfillment:** warm pools eliminate provisioning latency for the
  common case, making CI pipelines faster and more predictable.
- **Elastic scaling:** pools grow and shrink with demand, avoiding both resource
  waste and artificial queuing.
- **Unified user experience:** virtual and physical targets are leased through the
  same mechanism.
- **Kubernetes-native:** the model reuses Deployment/ReplicaSet/HPA/PDB/CSI idioms,
  so cluster admins need no new mental model, and standard tooling (`kubectl scale`,
  HPA/KEDA) interoperates.
- **Extensible backends:** new virtual providers (Renode, EC2, Corellium, Android,
  …) are added by defining a typed `*VirtualTarget` + a provisioner, without
  modifying the pool tier or existing providers.

### Negative

- **Increased operator complexity:** generic + per-provisioner controllers, scaling
  logic, and several CRDs add operational surface area.
- **Resource consumption:** warm pools consume cluster/backend resources even when
  idle; misconfigured `minAvailableReplicas` can lead to waste.
- **CRD count:** each backend adds a typed `*VirtualTarget` kind to manage and
  version.

### Risks

- **Scaling storms:** a burst of pending leases could trigger rapid scale-up; rate
  limiting mitigates this but may delay fulfillment under extreme load.
- **Provisioner reliability:** a backend that frequently fails to start can cause a
  tight crash-replace loop; backoff and conditions mitigate this.

## Rejected Alternatives

- **Static fixed-size pools (status quo):** cannot scale with demand.

- **A bespoke `*ExporterPool` CRD (earlier draft):** a single provider-typed pool
  CRD with a flat `labels:` field and custom scaling vocabulary. Rejected in favor
  of the Kubernetes-native split (`ExporterSet` + `VirtualTargetClass` + typed
  claim) so the system reads like native Kubernetes and reuses standard tooling.
  See DD-6, DD-12.

- **A single generic `VirtualTarget` with an opaque config map:** loses API-server
  schema validation and discoverability. See DD-3.

- **Per-lease `parameters` dictionary on the Lease CRD:** adds complexity to every
  layer for a use case served by separate sets/classes. See DD-4.

- **External orchestration (Terraform/Ansible):** pushes complexity to the user,
  breaks the single-pane-of-glass experience, and cannot integrate with lease
  semantics.

## Prior Art

- **Kubernetes Deployment/ReplicaSet/HPA, PodDisruptionBudget, CSI
  (StorageClass/PVC/PV), and Cluster API:** this JEP deliberately models its
  resources on these primitives (see DD-6, DD-12).

- **Crossplane:** its `ProviderConfig` pattern (a separate credential/account
  object referenced by managed resources) is the precedent for the deferred
  `*ProviderConfig` option (DD-11).

- **LAVA (Linaro Automated Validation Architecture):** supports virtual DUTs via
  QEMU but with static configuration; no on-demand scaling.

## Unresolved Questions

The following are design details to settle during implementation; none block the
overall model:

- Inline embedding/validation of typed `*VirtualTarget` claims within the
  `ExporterSet` Exporter template (`x-kubernetes-embedded-resource` vs. a
  discriminated union).
- The attach-mode driver protocol for container-backed split targets (QMP/serial
  over a shared volume), and per-provider rollout (today the QEMU driver
  `Popen`-spawns QEMU in-process).
- virtio device-type coverage and host-side backend wiring for SPI/CAN/GPIO/I2C,
  and the privilege trade-off (socket/vsock backends vs. `vcan`/`spidev`
  passthrough requiring privileged containers).
- For API-backed targets: where the external instance's lifecycle is owned, and how
  warm pools pre-provision vs. provision-on-lease.
- KVM access on the rendered Pod: device plugin (`devices.kubevirt.io/kvm`) vs.
  privileged container + `/dev/kvm`; how `scheduling.resources` maps to the exporter
  Pod vs. the runtime container; and merge semantics for class-vs-template
  scheduling overrides.
- How `ExporterSet.minAvailableReplicas` (pool size) and
  `VirtualTargetClass.bindingMode` (eager vs. lazy device realization) compose.
- `scale` subresource semantics with both HPA (ceiling) and the built-in controller
  (demand within bounds) active.
- The exact scaling algorithm (proportional, step-based, predictive).

### Resolved

- **Observability (JEP-0013):** controllers and virtual exporter instances emit
  metrics and logs using the mechanisms defined in JEP-0013; pool-specific metrics
  (pool size, available/leased counts, scale events) follow the same conventions.
- **Lease release detection:** the `ExporterSet` controller watches Lease objects
  directly; release transitions trigger scale-down evaluation.
- **Scheduled (future-dated) leases:** `jumpstarter-controller` already supports
  `Spec.BeginTime`; the controller does not acquire an exporter until `BeginTime`,
  so pools naturally do not scale up for future-dated leases until they become
  effective.

## Future Possibilities

The following are natural follow-ups enabled by this infrastructure but explicitly
**not** part of this JEP:

- **Additional backends:** `EC2VirtualTarget`, `RenodeVirtualTarget`, and others,
  each a typed claim + a provisioner.
- **A separate reusable `*ProviderConfig` CRD** (Crossplane / cert-manager `Issuer`
  style, optionally cluster-scoped) for multi-account credential reuse and rotation.
- **A realized-instance CRD (PersistentVolume analog)** for static/pre-provisioned
  devices and richer per-device lifecycle/status.
- **Multiple VirtualTargets per Exporter, spawned on lease:** multi-device virtual
  benches (e.g., a VM paired with a network emulator) as one leasable unit.
- **An `ExporterDeployment` rollout tier** (Deployment analog: revision history,
  rolling template updates) owning `ExporterSet`s.
- **Referenced `*VirtualTargetTemplate` blueprints** (Cluster API style) for
  reusable, immutable templates.
- **A universal `Target` abstraction** covering physical and virtual targets
  uniformly.

## Implementation Plan

The implementation is broken into phases. Each phase delivers a usable increment
and can be merged independently.

### Phase 1: Exporter `enabled` field

Add the `enabled` boolean to the Exporter CRD and update `jumpstarter-controller`
lease assignment to skip disabled exporters.

**Deliverables:**

- [ ] Add `spec.enabled` to the Exporter CRD (default: `true`).
- [ ] Filter out disabled exporters during lease assignment.
- [ ] Unit tests for the filtering logic.
- [ ] Integration test: disable an exporter, verify it gets no new leases.

**Why first:** small, self-contained, independently useful for lab maintenance, and
a prerequisite for graceful scale-down.

### Phase 2: `ExporterSet`, `VirtualTargetClass`, and the QEMU provisioner

Build the controller binary, define the generic `ExporterSet` and
`VirtualTargetClass` CRDs and the typed `QEMUVirtualTarget` claim, and implement the
core loops for the container backend.

**Deliverables:**

- [ ] Define the `ExporterSet` CRD (scaling fields, `selector`, inline `template`,
      status counters) with the `scale` subresource.
- [ ] Define the `VirtualTargetClass` CRD (`provisioner`, `credentialsSecretRef`,
      `parameters`, `bindingMode`, `reclaimPolicy`, `scheduling`, default-class).
- [ ] Define the `QEMUVirtualTarget` claim CRD.
- [ ] Implement the generic `ExporterSet` controller: warm-buffer maintenance,
      demand-driven scale-up, graceful drain (cordon → confirm → delete).
- [ ] Implement the `qemu.jumpstarter.dev` provisioner: render the instance Pod
      (exporter native sidecar + runtime container + OS image volume), apply class
      scheduling, register instances as standard Exporters.
- [ ] Add the `exporterControllers` section to the `Jumpstarter` operator CR; the
      operator deploys the generic controller + the qemu provisioner (RBAC, SAs,
      Deployment lifecycle).
- [ ] Unit + integration tests (deploy a set + class, verify warm pool, lease,
      release, scale behavior, `bindingMode`).

### Phase 3: Additional backends

Add support for further backends using the same binary with different `--provider`
flags.

**Deliverables:**

- [ ] `AndroidVirtualTarget` claim + `android` provisioner (container backend).
- [ ] `CorelliumVirtualTarget` claim + `corellium` provisioner (API backend),
      exercising `credentialsSecretRef` and `WaitForFirstConsumer`.
- [ ] Provisioner authoring guide (how to add a new `*VirtualTarget` + provisioner).

## Implementation History

- 2025-10-30: RFE filed upstream (GitHub #41)
- 2026-06-03: JEP proposed
- 2026-06-12: Re-modeled on Kubernetes workload primitives (ExporterSet /
  VirtualTargetClass / typed VirtualTarget claims), superseding the earlier
  `*ExporterPool` draft

## References

- [GitHub Issue #41: RFE: On-Demand Virtual Target Provisioning](https://github.com/jumpstarter-dev/jumpstarter/issues/41)
- [PITCREW-409: jumpstarter JEP: virtual scalable exporters](https://redhat.atlassian.net/browse/PITCREW-409)
- [JEP-0010: Renode Integration](JEP-0010-renode-integration.md) — Related provider
- [JEP-0013: Observability](JEP-0013-observability-telemetry-logs.md) — Integration point
- [Kubernetes CSI: StorageClass / PersistentVolumeClaim / PersistentVolume](https://kubernetes.io/docs/concepts/storage/persistent-volumes/)
- [Kubernetes image volumes (OCI artifacts as volumes)](https://kubernetes.io/docs/concepts/storage/volumes/#image)

---

This JEP is licensed under the
[Apache License, Version 2.0](https://www.apache.org/licenses/LICENSE-2.0),
