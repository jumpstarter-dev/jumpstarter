# JEP-0016: Cuttlefish Kubernetes-Native Orchestration

| Field             | Value                                                    |
| ----------------- | -------------------------------------------------------- |
| **JEP**           | 0016                                                     |
| **Title**         | Cuttlefish Kubernetes-Native Orchestration               |
| **Author(s)**     | @kirkbrauer (Kirk Brauer)                                |
| **Status**        | Draft                                                    |
| **Type**          | Standards Track                                          |
| **Created**       | 2026-08-29                                               |
| **Updated**       | 2026-08-29                                               |
| **Discussion**    | *TBD (PR link)*                                          |
| **Requires**      | JEP-0014                                                 |
| **Supersedes**    |                                                          |
| **Superseded-By** |                                                          |

---

## Abstract

This JEP proposes a `cuttlefish.jumpstarter.dev` provisioner for the JEP-0014
Virtual Scalable Exporter subsystem that runs
[Cuttlefish](https://github.com/google/android-cuttlefish) Android virtual
devices as native Kubernetes Pods. Each pool instance is a Pod pairing a
Cuttlefish Host Orchestrator runtime sidecar with a Jumpstarter exporter
running the existing `jumpstarter-driver-cuttlefish` driver, giving each
Android virtual device (CVD) the same lease, scaling, and recycling semantics
as any other `ExporterSet`-managed exporter. Because instances are ordinary
Pods with resource requests and device-plugin claims, Cuttlefish capacity
scales with the cluster autoscaler and MachineSets on Kubernetes and
OpenShift — no Google Cloud instances, Docker/Podman hosts, or external Cloud
Orchestrator service required. Google's Cloud Orchestrator Go packages are
reused as libraries (Host Orchestrator client, API types) rather than
deployed as a service.

## Motivation

Jumpstarter already has a Cuttlefish driver
(`jumpstarter-driver-cuttlefish`) that turns a host with a running
[Host Orchestrator](https://github.com/google/android-cuttlefish) into a
leasable Android virtual device: the driver creates, boots, resets, and
ADB-connects CVDs over the Host Orchestrator HTTP API. What is missing is
everything *around* that host:

- **Someone must provide the Cuttlefish host.** The Host Orchestrator manages
  CVDs *on the machine it runs on*; it does not provision machines, Pods, or
  containers. Today an administrator manually prepares a VM or Pod running
  the `cuttlefish-orchestration` image and statically registers an exporter
  against it.
- **Google's orchestration stack stops short of Kubernetes.** The
  [Cloud Orchestrator](https://github.com/google/cloud-android-orchestration)
  adds host provisioning on top of the Host Orchestrator, but its
  `instances.Manager` backends cover only GCE VMs, local Docker containers,
  and a single-host UNIX shim. There is no Kubernetes backend upstream, and
  the Cloud Orchestrator brings its own account management, database, and
  reverse-proxy layers that duplicate what Jumpstarter and Kubernetes already
  provide.
- **No elasticity.** Without pool management, Cuttlefish capacity is a fixed
  set of hand-built hosts — exactly the artificial-scarcity problem JEP-0014
  solves for QEMU. Android CI bursts (e.g. per-PR instrumentation test
  fan-out) either queue on a few static devices or waste idle warm hosts.
- **Cloud lock-in for scale.** Teams that want elastic Cuttlefish today are
  pushed toward the Cloud Orchestrator's GCE backend. Organizations running
  their own Kubernetes/OpenShift clusters (bare metal or any cloud) cannot
  reuse that capacity, their existing node autoscaling, or their cluster
  security and observability tooling.

### Agent-native device access

Cuttlefish's own repository underlines a growing consumer of ephemeral
devices: AI agents. Google's `podcvd` grew an MCP server (with a Gemini
extension), then replaced it with an agent skill, and added per-client
instance isolation (`PODCVD_CLIENT_ID`) specifically so concurrent agents
don't trample each other's devices — but all of it scoped to Cuttlefish on
a single host. Jumpstarter already has the general answer: a **lease** is
per-client isolation with authentication, expiry, and queueing, and the
same `jmp` CLI / Python API an agent drives to lease a Cuttlefish device
also leases a QEMU board, a cloud virtual device, or real hardware. This
JEP supplies the missing piece for the Android case — elastic, in-cluster
Cuttlefish capacity behind that one interface — so agent workflows (an
agent validating its change on virtual Android, then on a physical board)
compose across device types instead of being tool-per-device-type.

Just as importantly, the lease model **decouples where the agent runs from
where the device runs**. `podcvd` requires its caller on the device host
itself, with podman control and device ACLs — co-location that amounts to
host access, which is exactly what an agent sandbox is built to deny. A
Jumpstarter client needs only its lease credential and reachability to the
controller/router; every driver interaction (HO API, ADB, serial) tunnels
over authenticated gRPC streams. An agent can therefore run strongly
sandboxed — e.g. in a Kata container under a harness on an x86_64 node,
with Jumpstarter as its only capability — while its leased CVD runs with
native KVM on an ARM64 node elsewhere in the same private on-prem cluster.
The agent's blast radius is one expiring lease, not a device host; the
scheduler owns placement (x86_64 or arm64 pools per
`VirtualTargetClass.scheduling`), and the agent never knows or cares.

### User Stories

- **As an** Android platform CI author, **I want to** lease dozens of
  Cuttlefish devices for a test shard burst and have the cluster autoscaler
  add KVM-capable nodes as needed, **so that** my fan-out is bounded by
  cluster quota, not by a hand-built device farm.
- **As a** platform engineer on OpenShift, **I want to** declare an
  `ExporterSet` with `minAvailableReplicas: 5` of Cuttlefish exporters,
  **so that** developers get an Android device lease in seconds with a
  documented, auditable security posture (SCC, device plugins) instead of
  privileged pet VMs.
- **As a** developer, **I want to** lease a CVD, boot the Android build my PR
  produced, interact over ADB and WebRTC, and release it, **so that** I never
  wait on shared physical Android hardware for work a virtual device covers.
- **As an** AOSP platform developer using Android Studio for Platform,
  **I want** my leased CVD to appear in the IDE's device list and its
  screen in ASfP's built-in Cuttlefish webview via `j adb attach` +
  `j cuttlefish serve`, **so that** a cluster-hosted device is
  indistinguishable from a locally launched one in my daily workflow.
- **As a** cost-conscious operator, **I want** idle Cuttlefish pools to scale
  to zero Pods and the autoscaler to drain the KVM node pool, **so that**
  Android capacity costs nothing when unused.
- **As an** AI agent (or its operator), **I want to** lease an isolated
  Android virtual device through the same interface I use for QEMU boards
  and physical hardware, **so that** concurrent agent sessions never share
  or corrupt device state and an agent's workflow can span virtual and
  physical targets.
- **As a** security engineer running AI agents, **I want** agents confined
  to hardened sandboxes (e.g. Kata containers) whose only capability is a
  Jumpstarter client credential, **so that** an agent can drive a CVD
  running with native KVM on a remote ARM64 node in our on-prem cluster
  without ever holding device-host, container-runtime, or `/dev` access.

## Proposal

Add a **`cuttlefish.jumpstarter.dev` provisioner** to the exporter-set
controller introduced by JEP-0014. It implements the existing `Provisioner`
Go interface (`controller/internal/exporterset/provisioner.go`) — no new
CRDs, no changes to the lease flow. Administrators enable it exactly like the
QEMU provisioner:

```yaml
apiVersion: operator.jumpstarter.dev/v1alpha1
kind: Jumpstarter
spec:
  exporterSets:
    provisioners:
      - name: qemu.jumpstarter.dev
        enabled: true
      - name: cuttlefish.jumpstarter.dev
        enabled: true
```

### Instance model

Each pool instance is **one Pod = one Exporter = one Host Orchestrator = one
CVD** (see DD-2, DD-3). The governing invariant is Jumpstarter's own:
**an exporter is 1:1 with its DUT.** The Host Orchestrator can manage many
CVDs on its host, but this design deliberately never uses that capability —
inside a pool instance the HO is runtime plumbing (the way the QEMU
provisioner's runtime could technically run many machines and doesn't), not
a device-fleet surface. The driver is already built this way: it manages a
single named CVD group and treats extra CVDs on its HO as stale state to
remove.

This is upstream's own **container-per-DUT model** — the Cloud
Orchestrator Docker backend and `podcvd` both run one
`cuttlefish-orchestration` container per device — expressed in Kubernetes:
same image, same one-DUT-per-container boundary, with the kubelet in
podman's role and an exporter sidecar added for remote access. The
container boundary is what structurally enforces exporter = DUT, rather
than convention inside a shared host.

Seen at the right altitude, the roles map one level up: **the ExporterSet
provisioner is the Host Orchestrator of the cluster, and Pods are its
CVDs.** The HO creates, lists, and destroys device processes against one
host's resources; the provisioner creates, lists, and destroys device
Pods against the cluster's resources, with the scheduler and autoscaler
as its resource allocator and leases as its access control. What remains
of the HO inside each Pod is the per-device launcher shim — the `run_cvd`
wrapper and its localhost API — never a fleet manager.

The mapping composes into upstream's own two-tier architecture: Google's
stack is Cloud Orchestrator (manages hosts) → Host Orchestrator (manages
CVDs). Treating **each pool as one logical Host Orchestrator** reproduces
that layering with Jumpstarter as the implementation of both tiers — a
pool-level façade speaking the HO wire API backed by the ExporterSet, and
an `instances.Manager` backend mapping CO "hosts" onto pools — so
Google's clients (`cvdr`, the CO web UI) could one day drive Jumpstarter
pools natively. This stays future-track (see *Future Possibilities*);
under any such façade, every CVD remains exactly one Pod/exporter/lease.

In one sentence: **Jumpstarter becomes the cloud-native Cuttlefish
orchestrator** — the Cloud Orchestrator's role delivered through
Kubernetes primitives (Pods, the scheduler, the autoscaler, leases)
rather than a bespoke service, wire-compatible with Google's stack at
both of its API seams.

```text
ExporterSet cuttlefish-pixel
├── Exporter cuttlefish-pixel-aaa ──► Pod
│     ├── cvd               (native sidecar: single-CVD device runtime)
│     └── exporter          (main: jmp run + jumpstarter-driver-cuttlefish)
└── Exporter cuttlefish-pixel-bbb ──► Pod ...
```

- The **`cvd` runtime sidecar** is the device runtime: it runs the Host
  Orchestrator and cvd tools in service of exactly one CVD (the
  `cuttlefish-orchestration` image, or a Jumpstarter-built derivative). It is
  a native sidecar init container (`restartPolicy: Always`, KEP-753) so the
  HO API is up before the exporter starts, and it is torn down automatically
  when the exporter exits.
- The **exporter main container** runs `jmp run` with an `ExporterConfig`
  whose export map contains the existing `Cuttlefish` composite driver
  pointed at `127.0.0.1:2080` — the driver's control path is unchanged
  (see *Driver additions* for the small additive UI-serving surface); its
  deployment target moves from a hand-built host into the Pod.
- Exporter ↔ runtime communication is plain localhost HTTP (the HO API),
  simpler than the QEMU provisioner's Unix-socket protocol; no shared-socket
  volume or `jumpstarter-exec` staging is required. A shared `emptyDir` (or
  optional ephemeral PVC) holds CVD artifacts and runtime state.

### Example configuration

```yaml
apiVersion: virtualtarget.jumpstarter.dev/v1alpha1
kind: VirtualTargetClass
metadata:
  name: cuttlefish-x86-64
  namespace: jumpstarter
spec:
  provisioner: cuttlefish.jumpstarter.dev
  bindingMode: Immediate               # warm pool
  reclaimPolicy: Delete
  scheduling:
    nodeSelector:
      jumpstarter.dev/kvm: "true"      # KVM-capable node pool / MachineSet
    tolerations:
      - key: jumpstarter.dev/kvm
        operator: Exists
        effect: NoSchedule
    resources:
      requests:
        cpu: "4"
        memory: 8Gi
      limits:
        cpu: "8"
        memory: 8Gi
        devices.kubevirt.io/kvm: "1"
        devices.kubevirt.io/tun: "1"
        devices.kubevirt.io/vhost-net: "1"
  images:
    runtime:
      image: quay.io/jumpstarter-dev/virtual/cuttlefish-runtime:latest
  parameters:
    hostOrchestrator:
      port: 2080
    storage:
      size: 40Gi                       # artifact/runtime emptyDir size limit
    vsock:
      enabled: true                    # claims a vhost-vsock device (see DD-4)
---
apiVersion: virtualtarget.jumpstarter.dev/v1alpha1
kind: ExporterSet
metadata:
  name: cuttlefish-ci
  namespace: jumpstarter
spec:
  minReplicas: 0
  maxReplicas: 50
  minAvailableReplicas: 5
  scaleDownCooldown: 5m
  recycleStrategy: ExitAndReplace
  virtualTargetClassName: cuttlefish-x86-64
  selector:
    matchLabels:
      device: cuttlefish
  template:
    metadata:
      labels:
        device: cuttlefish
        os: android
        arch: x86_64
        virtual: "true"
    spec:
      drivers:
        - name: cuttlefish
          type: jumpstarter_driver_cuttlefish.driver.Cuttlefish
          config:
            boot_timeout: 600
```

No extra driver entries are needed for UI access — the `Cuttlefish` driver
exposes the operator web UI itself (see *Driver additions*).

### User experience

Unchanged from JEP-0014 — Cuttlefish exporters are ordinary pool members:

```bash
jmp lease -l device=cuttlefish,os=android
```

```python
with env() as client:
    client.cuttlefish.create_cvd(json.dumps({"env_config": {...}}))  # or power.on()
    client.cuttlefish.wait_boot()
    client.cuttlefish.adb.shell("getprop ro.build.fingerprint")
    print(client.cuttlefish.get_webrtc_url())
```

The lessee selects and boots the Android build after leasing (Build API
fetch via `env_config`, or user-artifact upload through the HO
`/v1/userartifacts` API) — the Cuttlefish analog of JEP-0014's
flash-at-lease model (DD-7 there, DD-6 here).

**Viewing the device:** every instance already ships the Host
Orchestrator's operator web UI (the WebRTC device-screen frontend built
into `cuttlefish-orchestration`). One command serves it locally — no
manual port juggling, no additional management service or UI deployment
(DD-9):

```bash
j cuttlefish serve
# Serving Cuttlefish UI at http://localhost:6080 (Ctrl+C to stop)
```

`serve` follows the same philosophy as `j adb attach` (PR #1033): meet the
client's existing tools where they are with zero configuration. It
forwards the leased instance's operator UI through the lease to
`localhost:6080` (`--port` to override, `--port 0` for an ephemeral port),
prints the URL, and tears the forward down cleanly on Ctrl+C. The Python
equivalent is a context manager:

```python
with client.cuttlefish.serve() as url:
    webbrowser.open(url)
```

### Driver additions

The `jumpstarter-driver-cuttlefish` driver gains two small, additive
pieces (useful for today's hand-managed Host Orchestrator hosts too, not
just this provisioner):

- **A `ui` network child**, auto-created in `__post_init__` exactly like
  the existing `adb` child: a `TcpNetwork` stream to the operator frontend
  (new `operator_port` config, default 1080). Because the driver owns the
  child, every Cuttlefish exporter exposes the UI with no template
  configuration.
- **A `serve` client command** on `CuttlefishClient` (CLI and Python
  context manager) that port-forwards the `ui` child to a local port
  (default 6080) and reports the URL. The existing `webrtc` command
  remains for printing the raw URL when the client has direct network
  reachability.

Both are backward compatible: existing configs need no changes, and the
new child appears alongside `power`/`storage`/`adb`.

**Android Studio for Platform (ASfP) integration.** ASfP ships a built-in
Cuttlefish webview that renders the operator UI, and standard IDE device
tooling rides the local ADB server. The integration contract is therefore:
*make the leased device indistinguishable from a locally launched
Cuttlefish.* Two commands achieve it, both already specified:

```bash
j adb attach          # leased CVD joins the IDE's own ADB server (PR #1033)
j cuttlefish serve    # operator UI served locally for the Cuttlefish webview
```

Confirmed from the cuttlefish sources: the UI is served by the
**operator** component (`frontend/src/operator/` — the Angular `webui`
device list plus WebRTC streaming and signaling), which runs alongside the
Host Orchestrator in the `cuttlefish-orchestration` container. Ports, per
`operator/main.go` and the `cuttlefish-operator` service defaults:

| Layout | UI address |
| --- | --- |
| Deb-installed operator (host installs and inside the Pod) | `http://:1080` / `https://:1443` |
| Standalone host tools (`launch_cvd` spawns its own operator) | `https://localhost:8443` |
| HO REST API (nginx 2080/2443 → HO 2081) | no UI — API + ADB websockets only |

The driver's `ui` child targets the in-Pod operator (1080), and a `ui-tls`
child targets its HTTPS listener (1443). `serve --port <n>` picks the
local port and `--tls` selects the HTTPS child, so either local layout is
reproducible verbatim — `j cuttlefish serve --tls --port 8443` for the
standalone convention, `--port 1443` for the deb convention. Certificate
trust for the operator's cert is an upstream-tracked concern
(android-cuttlefish PR #2819 added trusted-TLS support for operators on
container instances). The one fact still to pin against a real ASfP build
is which of these URLs (or a configurable one) the webview loads — see
Unresolved Questions.

**One lease vs. many.** ADB attachment is additive by design, so any
number of concurrently leased CVDs appear together in the IDE's device
list. The UI differs: locally, all CVDs register with one operator and
share one device list, whereas each leased Pod carries its own operator
listing exactly one device — so with multiple leases, each device gets its
own `serve` port and webview session. This mirrors upstream exactly:
`podcvd` gives each instance group a dedicated container IP and its
`fleet` subcommand merges the per-group `cvd fleet` JSON into one
inventory whose URLs point at each group's own operator — even upstream,
multi-group users open per-group UI pages; no operator aggregation
exists. The lease-spanning analogs (a `fleet` command over leases, and a
true operator-API aggregator) are specified under *Future Possibilities*;
a CVD group leased as one unit (DD-8) also shares one operator naturally.

**WebRTC media path.** Jumpstarter tunnels all lease traffic over gRPC
streams — a TCP transport — so UDP can never traverse a lease. Signaling
and the UI tunnel cleanly, but WebRTC *media* negotiates ICE candidates
that advertise the Pod's own address and the CVD's media port range
(15550–15599, per upstream defaults), predominantly UDP — `podcvd` works
by publishing exactly that range, TCP **and UDP**, on each container's
dedicated IP, which a TCP-only tunnel cannot reproduce. Two supported
modes follow directly from that constraint:

- **Direct reachability** (client can route to Pod IPs — the private
  on-prem/VPN case): media flows to the advertised candidates as-is, UDP
  included, outside the lease tunnel; the lease still carries control,
  ADB, and the UI.
- **Fully tunneled**: the runtime sidecar runs a TURN relay listening on
  TCP; `serve` forwards it alongside the UI port, and clients receive
  `turn:127.0.0.1:<port>?transport=tcp` in the operator's `/infra_config`
  response, so all media relays over TCP through the gRPC stream. The
  upstream operator currently hardcodes its ICE-server list (STUN only,
  `operator/main.go`), so the runtime image intercepts `/infra_config` at
  its proxy layer — or an `--ice_servers` flag is contributed upstream (a
  small, obviously useful patch).

### Scaling with the cluster autoscaler and MachineSets

The headline goal of this JEP is that Cuttlefish capacity behaves like any
other Kubernetes workload:

- Every instance Pod carries **real CPU/memory requests** (on the runtime
  sidecar, where the CVD runs) plus **extended-resource claims** for
  `/dev/kvm`, `/dev/net/tun`, and `/dev/vhost-net` from a device plugin.
- When the `ExporterSet` controller scales up beyond current node capacity,
  Pods go `Pending` on resources, which is precisely the signal the
  **cluster autoscaler** consumes to grow the KVM-capable node group
  (a MachineSet on OpenShift, a node group/pool elsewhere). Scale-down of
  idle exporters drains nodes and the autoscaler removes them.
- Node pools are targeted with the `VirtualTargetClass.scheduling`
  selector/tolerations — e.g. a dedicated tainted MachineSet of bare-metal
  or nested-virt instances labeled `jumpstarter.dev/kvm=true`.
- No cloud-specific code paths: the same manifests scale on OpenShift on
  bare metal, AWS, GCP, or Azure, because node provisioning is delegated to
  the cluster's own autoscaling machinery instead of a per-cloud
  `instances.Manager` backend.

### Relationship to Google's orchestration stack

| Layer | Google stack | This proposal |
| --- | --- | --- |
| Device runtime | `cvd` / Host Orchestrator on a host | Same, inside the runtime sidecar |
| Per-instance containers | `podcvd` (rootless podman on one host) | Pods rendered by the provisioner, scheduled by Kubernetes |
| Host provisioning | Cloud Orchestrator `instances.Manager` (GCE, Docker, UNIX) | JEP-0014 exporter-set controller rendering Pods |
| Fleet scaling | Manual / per-user host creation | `ExporterSet` warm pool + cluster autoscaler |
| Access & auth | Cloud Orchestrator reverse proxy + OAuth | Jumpstarter leases, router streams, existing authn |
| Go code reuse | — | `libhoclient` HO client, `apiv1` message types (see DD-7) |

Of the Google tools, **`podcvd`**
(`android-cuttlefish/container/src/podcvd/`) is the closest analog to this
proposal: it launches each Cuttlefish instance group in its own unprivileged
container for isolation, with exactly the device set and security posture
this JEP renders into Pods (see DD-5). It is host-local — a `cvd`-compatible
CLI shelling out to rootless podman on one machine prepared by
`podcvd-setup` — with no scheduling, pooling, leasing, or multi-host
awareness. This JEP is, in effect, the cluster-wide version of the same
per-instance-container model, with the kubelet playing podman's role and
the ExporterSet controller playing the operator's.

The client-side symmetry is the point: **`jmp` is to the cluster what
`podcvd` is to one host.** `podcvd create` spins up a container-per-DUT on
the local machine; `jmp lease` against a pool with
`minAvailableReplicas: 0` spins one up on demand anywhere the cluster has
capacity (warm pools trade that latency away when desired), with the
device landing wherever the scheduler places it — including on a
different architecture than the caller — behind an authenticated,
expiring lease instead of a client-ID label, from a CLI that also fronts
QEMU boards, cloud virtual devices, and physical hardware.

The Cloud Orchestrator itself is **not** deployed. Its
`instances.Manager` interface (`pkg/app/instances`) was evaluated as an
integration point; a Kubernetes backend for it remains a possible upstream
contribution (see *Future Possibilities*) but is not required for — nor
part of — this design (DD-1).

### API / Protocol Changes

**None to CRDs, protobufs, or the CLI.** This JEP is purely additive within
extension points JEP-0014 already defined:

- New provisioner string `cuttlefish.jumpstarter.dev` accepted by the
  exporter-set controller (`--provisioner` flag) and the operator's
  `spec.exporterSets.provisioners` list.
- New `parameters` conventions for that provisioner (interpreted only by it):
  `hostOrchestrator.port`, `storage.size`, `vsock.enabled`, plus defaults
  merged per JEP-0014 deep-merge rules.
- New container image: `cuttlefish-runtime` (Host Orchestrator packaging),
  versioned and overridable via the existing `images.runtime` field.
- `EnrichExporterExport` injects driver defaults (see Design Details); the
  `ExporterConfig` schema is unchanged.

### Hardware Considerations

Cuttlefish is a virtual target, but it has hard node requirements:

- **KVM:** `/dev/kvm` on every node in the target pool — bare metal or
  nested virtualization. Without KVM, Cuttlefish's crosvm/QEMU falls back to
  software emulation that is too slow to be useful; nodes without KVM are
  excluded via the class `nodeSelector`.
- **Devices:** `/dev/net/tun` and `/dev/vhost-net` for CVD networking;
  `/dev/vhost-vsock` optionally for vsock-based guest services. Exposed as
  extended resources by a device plugin (DD-4).
- **vsock CID contention:** vsock context IDs are host-global. Multiple
  Cuttlefish Pods on one node using default instance numbering can collide
  on CIDs (a known limitation of multi-container Cuttlefish on one host).
  Upstream's direction is userspace vsock (`--vhost_user_vsock=true`,
  tracked as b/383428636 in `podcvd`), which removes the shared
  `/dev/vhost-vsock` dependency entirely; see DD-4 / Unresolved Questions.
- **Architecture:** x86_64 CVDs on x86_64 nodes; arm64 host images exist
  upstream and arm64 node pools are in scope, but cross-architecture
  emulation is not.
- **GPU acceleration out of v1 scope:** graphics are software-rendered
  (SwiftShader) in v1. Upstream `podcvd` already passes NVIDIA GPUs into
  cuttlefish containers via CDI (`android.com/gpu-podcvd=all`), so the
  Kubernetes mapping — the NVIDIA device plugin with CDI — has a concrete
  path and is listed under *Future Possibilities*.

## Design Decisions

### DD-1: Orchestration locus — native provisioner vs. Cloud Orchestrator

**Alternatives considered:**

1. **Native Jumpstarter provisioner** — `cuttlefish.jumpstarter.dev`
   implements the JEP-0014 `Provisioner` interface and renders Pods
   directly; Cloud Orchestrator Go packages are reused as libraries only.
2. **Deploy the Cloud Orchestrator** — implement a Kubernetes
   `instances.Manager` backend upstream (or in a fork), run the Cloud
   Orchestrator as a cluster service, and have the Jumpstarter provisioner
   drive its REST API.
3. **Both, phased** — option 1 now, option 2 as a parallel upstream track.

**Decision:** Option 1 — native provisioner.

**Rationale:** The Cloud Orchestrator's value is host provisioning, fleet
listing, auth, and reverse proxying — all of which Jumpstarter and
Kubernetes already provide (ExporterSet scaling, Exporter registry, lease
auth, router streams). Deploying it would add a stateful service (accounts,
database, OAuth) between two systems that each want to own the same
lifecycle, and upstream has **no** Kubernetes backend today, so option 2
means landing greenfield code in a Google repo on their review timeline as a
prerequisite. Its backend selection is also a hardcoded `switch` in
`cmd/cloud_orchestrator/main.go` (no plugin registry), so even a merged
backend implies tracking their main binary. The genuinely reusable pieces —
the Host Orchestrator Go client (`libhoclient`) and API message types — are
importable libraries and are used per DD-7. An upstream `instances.Manager`
Kubernetes backend remains listed under *Future Possibilities* for
non-Jumpstarter users.

### DD-2: Pod topology — one CVD per Pod

**Alternatives considered:**

1. **1 CVD per Pod** — each Pod runs one HO managing exactly one CVD.
2. **Multi-CVD host Pods** — fewer, larger Pods each running an HO with N
   CVDs; one Exporter per CVD referencing a shared Pod.
3. **Configurable (`cvdsPerPod`)** — support both.

**Decision:** Option 1 — one CVD per Pod.

**Rationale:** First and foremost, Jumpstarter's model is that an
**exporter is 1:1 with its DUT** — one CVD is the device; the HO's
capacity for more is deliberately unused plumbing (see *Instance model*).
Mechanically, JEP-0014 models Exporter = leased unit = Pod, with
`ExitAndReplace` recycling implemented as main-container exit; a shared
multi-CVD Pod breaks that 1:1 lifecycle (one lessee's recycle would tear
down neighbors, or recycling degrades to in-place-only). Per-Pod resource
requests are what make bin-packing and cluster-autoscaler node scaling
accurate — a giant multi-CVD Pod turns the autoscaler's unit into "one
host", recreating the coarse granularity of VM-based farms. Isolation
between lessees (kernel namespaces, cgroups, seccomp) is also strictly
better per Pod. The cost — one HO process (~tens of MB) per CVD — is small
against a CVD's multi-GB footprint. Multi-CVD groups *within one lease* are
a separate need, deferred (DD-8 / Future Possibilities).

### DD-3: Host Orchestrator placement — sidecar vs. node-level service

**Alternatives considered:**

1. **HO as per-Pod runtime sidecar** — each instance Pod carries its own HO.
2. **HO as node DaemonSet** — one HO per node manages all CVDs on that node;
   exporter Pods are thin HTTP clients to the node-local HO.
3. **HO as standalone Deployment/Service** — central HO fleet, exporters
   connect remotely.

**Decision:** Option 1 — per-Pod native sidecar.

**Rationale:** The HO manages CVDs *on its own host* — with option 2 or 3
the "host" is the DaemonSet Pod, so CVD resources are consumed outside the
leased Pod's cgroup, breaking resource accounting, autoscaler signals, and
the OwnerReference cleanup cascade; a crashed node-level HO would take down
every CVD on the node. The sidecar model matches the QEMU provisioner
exactly (runtime sidecar + exporter main), inherits JEP-0014's teardown
semantics for free (exporter exit ⇒ Kubernetes stops the sidecar), and
scopes the elevated security context to precisely the Pods that need it
rather than a always-privileged DaemonSet. It also mirrors the layout the
upstream Docker backend produces (one `cuttlefish-orchestration` container
per host instance), keeping us on the tested upstream path.

### DD-4: Device access — device plugins vs. privileged hostPath

**Alternatives considered:**

1. **Device plugins with extended resources** — expose `/dev/kvm`,
   `/dev/net/tun`, `/dev/vhost-net` (e.g. the KubeVirt device plugins, as
   the QEMU provisioner already assumes for `devices.kubevirt.io/kvm`) and
   `/dev/vhost-vsock` via a generic device plugin; Pods claim them as
   resources.
2. **Privileged containers with hostPath mounts** — mount `/dev` paths
   directly and run privileged.
3. **Software-only fallback** — no KVM; rely on TCG emulation.

**Decision:** Option 1 — device plugins.

**Rationale:** Device plugins keep Pods unprivileged, make device capacity
**visible to the scheduler and cluster autoscaler** (a node advertising
`devices.kubevirt.io/kvm: 110` vs. one advertising none), and require no
blanket hostPath allowance in the SCC/PodSecurity policy. This is the same
dependency the QEMU provisioner documents, so clusters running JEP-0014
QEMU pools need nothing new for KVM/tun/vhost-net. `vhost-vsock` is the
exception: it is optional (`parameters.vsock.enabled`), claimed through a
generic device plugin, and — because CIDs are host-global — the device
plugin's advertised count doubles as the per-node concurrency limit for
vsock-enabled instances. Deterministic CID assignment is an Unresolved
Question. Privileged hostPath (option 2) remains a documented escape hatch
for clusters without device plugins, but is not the default rendering.
Option 3 is rejected as unusably slow for Android boot.

### DD-5: Security posture on Kubernetes and OpenShift

**Alternatives considered:**

1. **Targeted grants:** runtime sidecar runs with `NET_ADMIN` capability,
   `seccompProfile: Unconfined`, non-hostPath device-plugin devices; the
   exporter main container stays fully restricted (non-root, no added
   capabilities). On OpenShift, a dedicated `jumpstarter-cuttlefish` SCC
   bound to the pool's ServiceAccount grants exactly this.
2. **Privileged runtime container** — simplest, matches some community
   cuttlefish-on-K8s guides.
3. **Fully restricted** — no added capabilities, custom seccomp profile,
   user-mode networking.

**Decision:** Option 1 — targeted grants. This is the posture both upstream
container paths use: the Cloud Orchestrator Docker backend and `podcvd`
each run cuttlefish containers with `NET_ADMIN`, `seccomp=unconfined`, and
the four devices — explicitly *not* `--privileged`.

**Rationale:** Cuttlefish needs `NET_ADMIN` to create TAP devices and
bridges *inside the Pod's own network namespace* — no host networking is
used — and its syscall surface (KVM ioctls, vsock) exceeds the
`RuntimeDefault` seccomp profile. Privileged mode (option 2) grants far
more than needed and is a non-starter for many OpenShift environments.
Option 3 is the desirable end state and upstream is converging toward it:
`podcvd` marks `seccomp=unconfined` as temporary pending userspace vsock
becoming the default (`--vhost_user_vsock=true`, b/383428636), which would
let this provisioner drop both the unconfined profile and the
`/dev/vhost-vsock` claim in lockstep with upstream. Until then it is
tracked under *Future Possibilities* rather than blocking v1. The split posture keeps the
credential-holding exporter container restricted; only the device runtime
is relaxed. The operator ships the SCC and ServiceAccount when the
provisioner is enabled, so admins review one auditable object.

### DD-6: What a pool instance is — a booted device vs. a waiting host

**Alternatives considered:**

1. **Instance = HO-ready shell**: Ready = HO healthy + exporter
   registered; the CVD is created by the lessee (via `power.on` /
   `create_cvd` with their chosen build).
2. **Instance = the device**: a pool that pins a build via
   `parameters.envConfig` boots that CVD as the instance starts
   (`prewarm`); Ready converges on a **booted Android device**.

**Decision (revised):** Option 2 — the Pod is exactly one CVD, and when
the pool declares `parameters.envConfig`, enrichment injects
`prewarm: true` into the driver config (overridable in the template) so
the exporter boots the device as it starts. A pool without `envConfig`
degrades to option 1 (HO-ready shell; lessee boots), preserving the
flash-at-lease workflow where wanted.

**Rationale:** An earlier draft chose option 1 by analogy to JEP-0014
DD-7 (flash-at-lease, no admin-pinned images). The analogy is wrong for
Cuttlefish: **the Android build is the device's identity**, not a flashed
payload on separate hardware — a pool pinning `envConfig` is a rack of
one board type (JEP-0014 DD-4's pool-flavors pattern), not the rejected
image-refresh machinery. With prewarm, warm pools hold *booted devices*
and a lease is ADB-ready in seconds — the `podcvd create` experience at
cluster scale. Lessees lose nothing: `powerwash` gives clean state,
`power.off(destroy) + create_cvd` swaps in a custom build, and
per-build pools are separate `ExporterSet` flavors. Staleness is handled
by recycle (`ExitAndReplace` re-boots the pinned build fresh per lease).
A lease acquired mid-boot waits in `wait_boot` exactly as today.
Follow-on synergy: JEP-0015 dynamic exporter labels can surface boot
state and build identity as lease-matchable labels.

### DD-7: Go reuse — import `libhoclient` vs. reimplement

**Alternatives considered:**

1. **Import Google's Go client libraries** —
   `github.com/google/android-cuttlefish/frontend/src/libhoclient`
   (`HostOrchestratorClient`: CVD CRUD, operations wait, user artifacts,
   ADB/WebRTC connections) and the `apiv1` message types for HO/CO APIs.
2. **Reimplement a minimal HO HTTP client** in the controller.
3. **Implement the Cloud Orchestrator's `instances.Manager` interface
   shape** inside Jumpstarter as a compatibility layer.

**Decision (revised after prototyping):** **No HO client in the
controller at all** — the provisioner's `RenderPod`/`EnrichExporterExport`/
`Cleanup` only render Pod specs and never call the Host Orchestrator at
runtime; readiness is an HTTP probe on the runtime sidecar and recycle-time
`/reset` is driven by the exporter (the Python driver), not the
controller. `libhoclient` (or its `FakeHostOrchestratorClient`) remains an
option for **e2e tests only**. Option 3 is rejected: Jumpstarter's
`Provisioner` interface is the plug point (per DD-1), and
`instances.Manager`'s host-centric contract (zones, per-user hosts,
operations) doesn't map onto Pod rendering.

**Rationale:** An earlier draft chose option 1, but an empirical probe
during prototyping (2026-08-29) reversed it: `libhoclient@main` resolves
as a Go module but compiles only when three sibling modules
(`libhoclient`, `host_orchestrator`, `liboperator`) are hand-pinned to the
same pseudo-version — upstream tags only the parent repo, so any
dependency bump that moves one module without the others breaks the
build — and it pulls the full pion WebRTC stack (~17 modules) into the
controller's supply chain for RPCs a provisioner never calls. Since the
render-only design needs no HO client in the controller, the import buys
nothing at real cost. The Python driver remains the runtime HO client, as
today.

### DD-8: Multi-CVD lease groups

**Alternatives considered:**

1. **Defer** — v1 leases exactly one CVD; note groups under Future
   Possibilities.
2. **In scope** — design the parameters/driver surface for leasing a CVD
   group (multi-device Bluetooth/Wi-Fi topologies) now.

**Decision:** Option 1 — defer.

**Rationale:** Multi-device topologies align with JEP-0014's deferred
"multiple/spawned-on-lease VirtualTargets per Exporter" and composite
leases; solving them only for Cuttlefish would fragment the model.

Any future group mode must also reconcile with the exporter = DUT
invariant (see *Instance model*), and there are exactly two honest ways to
do that:

1. **Composite leases across single-CVD exporters** — preserves strict
   per-device 1:1; each device stays its own Pod/exporter, and the lease
   layer binds N of them. Requires a cross-Pod virtual-radio story
   (rootcanal/netsim connectivity between instances in different Pods) —
   real upstream-facing work.
2. **A CVD group as one composite DUT** — one Pod, one exporter, one
   lease, where the *bench* is the DUT (the way a physical exporter can
   front a board plus its peripherals). The HO then manages the group's
   members as internals of a single device, which keeps the exporter
   model honest at the cost of coarser leasing granularity.

What is **not** acceptable is N independently leased devices behind one
exporter — that breaks the 1:1 model outright. Deferring keeps both
legitimate paths open.

### DD-9: Device UI — per-Pod operator UI vs. Cloud Orchestrator web UI

**Alternatives considered:**

1. **Reuse the per-Pod Host Orchestrator operator UI** — the WebRTC
   device-screen frontend already inside every `cuttlefish-orchestration`
   container; lessees reach it by forwarding its port through their lease.
2. **Adopt the Cloud Orchestrator management UI and tenancy** — deploy the
   CO service with a lease-backed `instances.Manager` adapter so its
   fleet web UI (`web/page0`), `cvdr` CLI, and per-user host model front
   Jumpstarter.
3. **Build a Jumpstarter fleet UI** for virtual devices.

**Decision:** Option 1 for v1 — minimal reuse, no management service,
packaged as a one-command client experience (`j cuttlefish serve`, see
*Driver additions*).

**Rationale:** The operator UI ships in the image this JEP already deploys,
is scoped to exactly one leased device, and inherits lease authentication
for free — the forward exists only inside the lessee's session, so no new
service, identity mapping, or ingress is introduced. The CO management
layer (option 2) is architecturally feasible — `app.NewApp` accepts any
`instances.Manager`, and CO "hosts" map cleanly onto leases
(`CreateHost` → lease, `ListHosts` → the user's leases, `GetHostClient` →
a router tunnel to the leased Pod's HO) — but it brings a second identity
system (`accounts.Manager` is Google-IAP/GAE-oriented), a database, and a
per-user host-ownership model that duplicates what leases and cluster
RBAC already provide, all to serve only the Cuttlefish slice of a
heterogeneous fleet. It is recorded under *Future Possibilities* as an
optional frontend adapter, with Jumpstarter remaining the source of truth.
Option 3 is out of scope for a provisioner JEP. Caveat shared with any
option: WebRTC media prefers UDP/ICE, so interactive streaming through a
TCP forward depends on the operator's TCP fallback — see Unresolved
Questions.

## Design Details

### Provisioner implementation

`controller/internal/exporterset/provisioners/cuttlefish/` implements the
three-method `Provisioner` interface:

**`RenderPod`** renders (mirroring the QEMU provisioner's structure):

```yaml
spec:
  restartPolicy: Never                     # ExitAndReplace: exporter exit completes the Pod
  initContainers:
    - name: cvd                            # native sidecar (KEP-753)
      image: quay.io/jumpstarter-dev/virtual/cuttlefish-runtime:<version>
      restartPolicy: Always
      securityContext:
        capabilities:
          add: ["NET_ADMIN"]
        seccompProfile:
          type: Unconfined
      resources:                           # from VirtualTargetClass.scheduling.resources
        requests: { cpu: "4", memory: 8Gi }
        limits:
          cpu: "8"
          memory: 8Gi
          devices.kubevirt.io/kvm: "1"
          devices.kubevirt.io/tun: "1"
          devices.kubevirt.io/vhost-net: "1"
      startupProbe:
        httpGet: { path: /_debug/statusz, port: 2080 }
        periodSeconds: 2
        failureThreshold: 60
      readinessProbe:
        httpGet: { path: /_debug/statusz, port: 2080 }
      volumeMounts:
        - { name: cuttlefish-state, mountPath: /var/lib/cuttlefish }
  containers:
    - name: exporter                       # main; restricted; default kubectl logs
      image: quay.io/jumpstarter-dev/jumpstarter:<version>
      command: ["jmp", "run", "--exporter-config", "/etc/jumpstarter/exporters/config.yaml"]
      securityContext:
        runAsNonRoot: true
  volumes:
    - name: cuttlefish-state
      emptyDir:
        sizeLimit: 40Gi                    # parameters.storage.size
```

Differences from the QEMU provisioner, by design:

- **No `copy-jumpstarter-exec` init container and no launcher socket** —
  control is localhost HTTP to the HO, so the shared volume carries only
  CVD state, not a socket protocol.
- **Probes on the runtime sidecar** gate exporter start on HO API
  availability (native sidecars support startup/readiness probes), instead
  of socket-existence ordering.
- **CVD resource sizing is Pod-level** (`scheduling.resources` on the
  sidecar), while per-boot Android configuration flows through the driver's
  `env_config` at lease time.

The reconciler's existing behavior is reused unchanged: two-phase creation
(Exporter CR first, Pod after credentials), config-volume injection,
OwnerReferences, `exitOnLeaseEnd` derivation from `recycleStrategy`.

**`EnrichExporterExport`** injects into the `Cuttlefish` driver entry (never
overriding explicit template values):

- `host: 127.0.0.1`, `port` from merged `parameters.hostOrchestrator.port`
  (default 2080);
- `boot_timeout` default;
- driver-level defaults from merged parameters (e.g. a default `env_config`
  block if the class provides one for convenience — still overridable by
  the lessee via `create_cvd`).

It does not auto-inject wrapper drivers in v1 — UI access needs none,
because the driver's own `ui` child (see *Driver additions*) covers the
operator frontend; enrichment only sets `operator_port` from parameters if
overridden.

**`Cleanup`** is a no-op for in-cluster resources (OwnerReference cascade
deletes the Pod). For `InPlaceReuse` recycling, the exporter drives
`POST /reset` on the HO (already exposed as `reset_host` by the driver)
before returning to Ready.

### Instance lifecycle

```text
Pod scheduled ─► cvd sidecar starts ─► HO /_debug/statusz OK
  ─► exporter starts, registers
  ─► prewarm (pool pins envConfig): device boots in background ─► Ready (booted device)
     no envConfig: Ready (HO-ready shell; lessee boots at lease)
  ─► leased ─► session (mid-boot lease waits in wait_boot)
  ─► lease released ─► ExitAndReplace: exporter exits ─► Pod completes
  ─► controller replaces instance (fresh Pod re-boots the pinned build)
```

`ExitAndReplace` (default) guarantees a pristine HO and empty artifact
volume per lease — no cross-lease Android state leakage. `InPlaceReuse`
trades that for skipping Pod churn: `reset_host` deletes all CVDs and
scrubs HO state, but the artifact cache survives, which is exactly what a
CI pool re-fetching the same build wants.

### Failure modes

- **Runtime sidecar crash mid-lease:** native sidecar restarts in place
  (its `restartPolicy: Always`); the CVD dies with it. The driver surfaces
  `CuttlefishError` on the next call; the lessee recovers with
  `power.on` (recreate) or releases the lease. The exporter stays
  registered, so lease semantics are preserved.
- **Pod evicted / node drained:** standard JEP-0014 handling — Pod failure
  detected, instance replaced, lease (if held) fails visibly rather than
  silently migrating; virtual devices are not live-migrated.
- **HO operation timeouts:** the driver's existing operation wait-loop
  handles 503/504/long-poll; the provisioner adds nothing.
- **Scheduling starvation (no KVM nodes):** Pods stay `Pending`; the
  ExporterSet reports `ScalingLimited`/unhealthy conditions per JEP-0014,
  and the pending Pods trigger the cluster autoscaler where configured.
- **Device-plugin absence:** Pods stay `Pending` on unsatisfiable extended
  resources — surfaced in ExporterSet conditions with an actionable event
  rather than a runtime failure inside a scheduled Pod.

### Security summary

| Container | Posture |
| --- | --- |
| `cvd` (sidecar) | `NET_ADMIN`, `seccompProfile: Unconfined`, device-plugin devices, root inside container, Pod-scoped netns (no hostNetwork), no hostPath |
| `exporter` (main) | non-root, no added capabilities, `RuntimeDefault` seccomp, holds exporter credentials |

On OpenShift the operator ships a `jumpstarter-cuttlefish` SCC (allowing
the sidecar's capability/seccomp/device profile) bound to the
ServiceAccount used by rendered Pods, enabled only when the provisioner is
enabled. On vanilla Kubernetes, the namespace needs Pod Security admission
`privileged` (namespace-scoped) or an equivalent policy exception scoped to
these Pods; this is documented with the provisioner.

## Test Plan

### Unit Tests

- Provisioner `RenderPod` rendering: device claims, probes, security
  contexts, parameters/image merge, storage size limit (mirrors
  `provisioners/qemu/qemu_test.go`).
- `EnrichExporterExport` injection and no-override guarantees.
- Parameter validation (port ranges, storage quantity parsing,
  `vsock.enabled` device claim toggling) surfaced as conditions.

### Integration Tests

- Kind e2e suite labeled `exporterset-cuttlefish` (per
  `.claude/rules/e2e-doc-sync.md`, added to `e2e/README.md` in the same
  PR): apply class + set, verify Exporter/Pod render and registration.
  Kind nodes lack `/dev/kvm` in CI, so the e2e path uses a stub/HO-only
  mode validating control-plane behavior (Pod shape, readiness gating,
  scale up/down, recycle), mirroring how `exporterset-qemu` gates
  flash/boot on storage availability.

### Hardware-in-the-Loop Tests

- Full CVD boot requires a KVM-capable runner (bare metal or nested virt):
  lease → `create_cvd` (small known build) → `wait_boot` → `adb shell` →
  release → verify ExitAndReplace produced a fresh instance. Runs on a
  labeled self-hosted runner; documented as manual until such a runner is
  in CI.

### Manual Verification

- OpenShift: SCC binding applied by the operator; rendered Pods admitted
  without cluster-wide privileged access; `oc get exporterset` status
  counters correct across a scale-up/lease/release cycle.
- Cluster autoscaler: with a capped KVM MachineSet, scale an ExporterSet
  past node capacity and observe node scale-out and scale-in.

## Acceptance Criteria

- [ ] `cuttlefish.jumpstarter.dev` provisioner implemented behind the
      existing `Provisioner` interface; no CRD schema changes
- [ ] `cuttlefish-runtime` image built and published for x86_64 (arm64
      stretch), versioned with the controller release
- [ ] Warm pool of ready device instances maintained per
      `minAvailableReplicas`; demand scale-up and cooldown scale-down work
      per JEP-0014 semantics
- [ ] A pool declaring `parameters.envConfig` prewarms: instances boot the
      pinned build at start (DD-6) and a lease is ADB-ready without the
      lessee creating a CVD; a pool without `envConfig` behaves as an
      HO-ready shell with lessee-driven boot
- [ ] Existing `jumpstarter-driver-cuttlefish` driver works against the
      in-Pod HO with no template configuration beyond the driver entry
      (lease → create → boot → adb → release)
- [ ] `j cuttlefish serve` serves the operator web UI at
      `http://localhost:6080` by default through the lease, and tears
      down cleanly on Ctrl+C; Python `client.cuttlefish.serve()` context
      manager equivalent
- [ ] A leased CVD appears natively in Android Studio for Platform:
      visible in the IDE's device list via `j adb attach`, and rendered in
      the built-in Cuttlefish webview against the served operator UI
      (`serve` in local-layout mode matching the address ASfP loads) —
      validated against a current ASfP release
- [ ] Multiple concurrent leases coexist natively on one client: all
      attached CVDs listed together by the IDE's ADB device list, each
      viewable via its own `serve` session on a distinct port
- [ ] Rendered Pods run unprivileged with only `NET_ADMIN` +
      seccomp-unconfined on the runtime sidecar; exporter container fully
      restricted
- [ ] OpenShift SCC shipped by the operator and required grants documented
- [ ] Device-plugin resource claims render from class scheduling +
      `vsock.enabled`; missing plugins surface as ExporterSet conditions
- [ ] Pending Pods from pool scale-up trigger cluster-autoscaler node
      scale-out in a documented reference setup (MachineSet example)
- [ ] `ExitAndReplace` yields a pristine HO per lease; `InPlaceReuse`
      resets via `/reset`
- [ ] e2e `exporterset-cuttlefish` suite green in CI; `e2e/README.md`
      updated in the same PR
- [ ] Documentation: provisioner guide with class/set examples, security
      posture, node prerequisites, autoscaler integration

## Graduation Criteria

### Experimental

- Provisioner functional end-to-end on one KVM-capable dev cluster
  (Kubernetes and OpenShift); boot/lease cycle validated manually
- Feedback gathered on parameters surface and security posture

### Stable

- CI coverage including a KVM-capable runner for real CVD boots
- Production-style usage by at least one Android CI consumer for >1 month
- Documented benchmarks: warm-lease latency, lease-to-adb latency (cold
  fetch vs. cached), instances-per-node density

## Backward Compatibility

Fully additive:

- No changes to existing CRDs, the gRPC protocol, the lease flow, or the
  QEMU provisioner.
- The `jumpstarter-driver-cuttlefish` Python driver changes are additive
  only (the `ui` child and `serve` client command; new `operator_port`
  config with a matching default): existing configs keep working against
  hand-managed Host Orchestrator hosts exactly as today, and gain
  `j cuttlefish serve` for free.
- Clusters that never enable `cuttlefish.jumpstarter.dev` see no new
  objects, images, or SCCs.

## Consequences

### Positive

- **Elastic Android device capacity** on any Kubernetes/OpenShift cluster,
  scaled by the cluster autoscaler and MachineSets rather than
  cloud-provider-specific host provisioning.
- **Second real provisioner** validating JEP-0014's pluggable model with an
  HTTP-managed runtime; exercises the interface beyond QEMU's socket model.
- **Reuses the shipped driver unchanged** — one code path from a developer
  laptop HO to a 50-Pod CI pool.
- **Documented, minimal security posture** replacing ad-hoc privileged
  cuttlefish deployments.
- **No new stateful services** — no Cloud Orchestrator, database, or OAuth
  deployment to operate.

### Negative

- **Elevated (if targeted) privileges remain:** seccomp-unconfined +
  `NET_ADMIN` on the runtime sidecar will be unacceptable in some hardened
  clusters until a tailored profile exists.
- **Node prerequisites:** KVM-capable nodes and device plugins are hard
  requirements; clusters without them get nothing from this provisioner.
- **New image to maintain:** the `cuttlefish-runtime` image tracks upstream
  `android-cuttlefish` releases (HO API drift, security updates).
- **Lease-time boot latency:** minutes for cold artifact fetch + boot,
  inherent to DD-6's flash-at-lease model, until caching lands.

### Risks

- **Upstream drift:** HO API or `cuttlefish-orchestration` image behavior
  changes could break the runtime image or `libhoclient` pin; mitigated by
  version-pinning the runtime image to tested upstream releases.
- **vsock CID collisions** could cap per-node density or cause flaky
  multi-instance nodes if the allocation question resolves badly
  (worst case: vsock-enabled pools limited to one instance per node via the
  device plugin's advertised capacity).
- **`libhoclient` module consumption** may prove awkward (subdirectory
  module); fallback is a minimal internal client (DD-7).

## Rejected Alternatives

- **Deploy the Cloud Orchestrator with a new Kubernetes backend** — see
  DD-1; duplicates Jumpstarter/Kubernetes responsibilities and depends on
  greenfield upstream work.
- **Keep using GCE/Docker via Google's stack** (status quo for scale) —
  cloud lock-in, no reuse of cluster autoscaling, capacity invisible to
  Jumpstarter pooling.
- **Node-level Host Orchestrator DaemonSet** — see DD-3; breaks resource
  accounting and lifecycle ownership.
- **Wrapping `podcvd` on nodes** (e.g. a DaemonSet or lab agent shelling
  out to `podcvd`/podman) — runs a second container runtime beside the
  kubelet, hiding CVD containers from the scheduler, the cluster
  autoscaler, resource quotas, and OwnerReference cleanup. `podcvd` is the
  right tool for a single developer host; on Kubernetes the kubelet already
  fills its role.
- **Multi-CVD host Pods / configurable density** — see DD-2.
- **Privileged Pods with hostPath devices** — see DD-4/DD-5; kept only as a
  documented escape hatch, not the rendered default.
- **A separate Cuttlefish-specific pool CRD** — rejected on JEP-0014
  grounds (DD-3 there): provisioner string + `parameters`, not new kinds.
- **Cloud Orchestrator management UI and tenancy (for v1)** — see DD-9;
  duplicates lease/RBAC tenancy with a Google-account-oriented identity
  layer and adds a stateful service for one device type. The per-Pod
  operator UI covers device viewing; a lease-backed UI adapter stays a
  future possibility.

## Prior Art

- **JEP-0014 QEMU provisioner** — the reference for Pod rendering, sidecar
  pattern, and scaling; this JEP deliberately mirrors it.
- **Google `podcvd`** (`android-cuttlefish/container/src/podcvd/`) — the
  closest upstream analog: a `cvd`-compatible CLI running each instance
  group in its own unprivileged rootless-podman container (same image,
  devices, `NET_ADMIN`, seccomp posture as this JEP; per-container IPs via
  pasta; NVIDIA GPU via CDI). Its stated motivation — instance groups "not
  to interfere host environment of each other" — is the same host-state
  interference class the Jumpstarter Cuttlefish driver currently works
  around with reset-and-retry guidance; per-instance isolation eliminates
  it structurally. Actively developed since January 2026 (originally
  `cvdexec`), Debian-packaged and CI-gated in the `cuttlefish-container`
  release train, with per-client instance isolation (`PODCVD_CLIENT_ID`)
  added for concurrent AI-agent workflows — a single-host cousin of
  Jumpstarter leases. Single-host and interactive — no pooling, leasing,
  or multi-node scheduling; it confirms per-group unprivileged containers
  as upstream's strategic direction, which this JEP scales cluster-wide.
- **Google Cloud Orchestrator** (`instances.Manager`, GCE/Docker/UNIX
  backends) — the host-provisioning prior art this design replaces with
  Kubernetes-native scheduling; its Docker backend defines the container
  requirements adopted here.
- **AOSP on-premise Cuttlefish guidance** (Docker single-server) — same
  runtime containers, manual orchestration.
- **Android emulator container scripts / community cuttlefish-on-K8s
  posts** — demonstrate feasibility, typically with privileged Pods and no
  pooling/leasing; this JEP productizes the pattern with a minimal
  security posture.
- **KubeVirt device plugins** — established mechanism for exposing
  `/dev/kvm`, `/dev/net/tun`, `/dev/vhost-net` as schedulable resources.

## Unresolved Questions

- **vsock CID allocation:** how to avoid host-global CID collisions between
  Cuttlefish Pods sharing a node. Candidate resolutions: default to
  userspace vsock (`--vhost_user_vsock=true`, upstream `podcvd`'s
  direction per b/383428636 — no `/dev/vhost-vsock` claim at all),
  deterministic per-Pod `--vsock_guest_cid` from an allocated index, or a
  device-plugin capacity of 1. Leaning `vhost_user_vsock` by default with
  `parameters.vsock.enabled` opting into the kernel device.
  (Implementation-time; does not block the design.)
- ~~**`libhoclient` dependency mechanics**~~ — resolved by the 2026-08-29
  prototype probe; see DD-7 (no controller import; sibling-module
  pseudo-version pinning and pion dependency drag documented there).
- **WebRTC media over the lease forward:** the design direction is set
  (see *WebRTC media path*: direct-reachability mode, or in-Pod
  TURN-over-TCP for fully tunneled leases — gRPC's TCP transport rules
  out UDP candidates). To validate at implementation: relay latency and
  frame rate over TURN/TCP through the gRPC stream, whether the
  `/infra_config` intercept suffices or the upstream `--ice_servers` flag
  should be contributed first, and which TURN implementation rides in the
  runtime sidecar (coturn vs. a minimal embedded relay).
- **`cuttlefish-runtime` image contents:** thin wrapper over upstream
  `cuttlefish-orchestration` vs. Jumpstarter-built image from the upstream
  Debian packages (base/user/orchestration) for supply-chain control.
- **Exporter readiness vs. HO readiness:** should the exporter delay
  registration until a deeper HO check (e.g. `cvd version`) passes, to keep
  "Ready" honest beyond a 200 from `statusz`?
- **ASfP webview URL:** the webview renders the operator UI (confirmed:
  the operator component owns the UI; the android-cuttlefish repo contains
  no ASfP-specific hooks, so the contract lives on ASfP's side). Which
  address does it load — standalone-layout `https://localhost:8443`,
  deb-layout `:1443`/`:1080`, or a configurable URL? Verify against a
  current ASfP build and set `serve`'s local-layout defaults (port, TLS,
  certificate trust via the upstream trusted-TLS operator work,
  android-cuttlefish PR #2819) to match.

## Future Possibilities

Explicitly **not** part of this proposal:

- **Upstream Kubernetes `instances.Manager` backend** contributed to
  `google/cloud-android-orchestration`, sharing the Pod-rendering logic, so
  non-Jumpstarter users get Kubernetes hosts too.
- **Pool-as-Host-Orchestrator façade** — a pool-level service speaking
  the upstream HO wire API backed by the `ExporterSet`: `GET /cvds` lists
  the pool's instances, `POST /cvds` acquires a lease and boots the
  request's `env_config` on it (demand-driven scale-up = on-demand
  creation), `DELETE` releases/recycles, the operations API surfaces
  lease/boot progress, and per-device ADB/WebRTC endpoints proxy to each
  Pod's shim. `env_config` passes through opaquely (it is upstream's
  unstable "black box"; the façade never interprets it). Open design
  points: the HO API is unauthenticated by design (CO fronts auth), so
  the façade must hold a Jumpstarter client credential and scope device
  listings per caller to avoid cross-lessee leakage; and HO
  group/instance-number semantics must map onto N single-CVD instances.
  The exporter = DUT invariant is untouched — the façade is a view, not a
  topology change — and the deferred multi-device UI aggregation falls
  out of it (one "host" listing N devices).

  The façade also gives the controller a **native downward control
  plane**: the same per-Pod HO API `podcvd` uses per-container becomes
  how the orchestrator speaks to its devices — backing the façade's
  calls, driving `POST /reset` for `InPlaceReuse` recycling, and
  polling device state for boot-gated readiness / JEP-0015 dynamic
  labels. This deliberately revisits DD-7's render-only stance, in a
  dedicated component outside the reconcile loop (reconciles must never
  block on long HO operations), with a NetworkPolicy restricting the
  unauthenticated per-Pod HO port to the controller/façade.
- **Cloud Orchestrator backend over pools** (DD-9 option 2, refined by
  the façade above) — an `instances.Manager` implementation mapping CO
  "hosts" onto pools, with `GetHostClient` returning the upstream
  `NetHostClient` pointed at the pool façade and `accounts.Manager`
  implemented against cluster OIDC. Because the façade speaks the real HO
  API, `cvdr` and the CO web UI would drive Jumpstarter pools natively,
  reproducing Google's CO → HO two-tier architecture with Jumpstarter as
  both tiers' implementation.
- **CVD groups / multi-device leases** (DD-8) — Bluetooth/Wi-Fi topologies,
  via composite leases across single-CVD exporters or a group modeled as
  one composite DUT; never N independently leased devices behind one
  exporter (the exporter = DUT invariant).
- **Artifact caching** — node-level or PVC-backed cache of Android build
  artifacts (content-addressed via the HO user-artifacts API) to cut
  lease-to-boot latency; possibly a pool-level pre-fetch hook.
- **Boot-gated readiness** — registering the exporter (or surfacing a
  JEP-0015 dynamic label) only once the prewarmed device is fully booted,
  so `availableReplicas` counts booted devices rather than
  booting-in-background ones; needs an exporter-level lifecycle hook.
- **Restricted seccomp** for the runtime sidecar — retire `Unconfined`
  once upstream makes userspace vsock the default (b/383428636), or via a
  tailored profile (DD-5 option 3).
- **GPU acceleration** via the NVIDIA device plugin/CDI, following the CDI
  integration `podcvd` already ships for single hosts.
- **arm64 pools** on arm64 MachineSets using upstream arm64 host images.
- **Snapshot/restore boot acceleration** using the HO snapshots API.
- **Agent-facing skill for Jumpstarter leases** — a `SKILL.md`-style guide
  (following `podcvd`'s MCP-to-skill precedent) teaching coding agents the
  `jmp lease` → driver → release workflow across device types, with
  Cuttlefish pools as the elastic Android tier.
- **Cross-lease `fleet` command** — the lease-spanning analog of
  `podcvd fleet`: enumerate the client's active Cuttlefish leases, ensure
  a `serve` session per lease on deterministic local ports, attach ADB,
  and emit one merged listing (lease → adb serial → local UI URL) —
  `podcvd`'s exact merge-and-rewrite pattern with lease tunnels in place
  of per-container IPs.
- **Local operator aggregator** — one step further: a client-side,
  operator-API-compatible endpoint (the client surface is small —
  `/devices`, `/devices/{id}/connect`, `/polled_connections`,
  `/infra_config`) that merges device lists across leases, prefixes
  device IDs per lease, and proxies signaling to each Pod's operator, so
  one browser tab or ASfP webview shows all leased CVDs like a local
  multi-device host.
- **UDP datagram streaming in the Jumpstarter router** (separate JEP —
  protocol change to `router.proto`): the `Stream` RPC is already
  frame-based (`StreamRequest{payload, frame_type}`), so an additive
  `FRAME_TYPE_DATAGRAM` (one datagram per frame, plus a client-side UDP
  port-forward adapter) would give `UdpNetwork` faithful datagram
  boundaries through leases — benefiting UDP device protocols, DTLS, and
  telemetry generally. Honest scope note: the substrate remains
  gRPC/HTTP-2/TCP, so this does not remove head-of-line blocking — for
  WebRTC media latency it is equivalent to the TURN-over-TCP path above,
  and true unreliable delivery would need a QUIC/HTTP-3 datagram
  transport, a larger follow-on.

## Implementation History

- 2026-08-29: Initial draft

## References

- [JEP-0014: Virtual Scalable Exporters](JEP-0014-virtual-scalable-exporters.md)
- [google/android-cuttlefish](https://github.com/google/android-cuttlefish) —
  Host Orchestrator
  ([controller routes](https://github.com/google/android-cuttlefish/blob/main/frontend/src/host_orchestrator/orchestrator/controller.go),
  [libhoclient](https://github.com/google/android-cuttlefish/blob/main/frontend/src/libhoclient/host_orchestrator_client.go),
  [container README](https://github.com/google/android-cuttlefish/blob/main/container/README.md),
  [podcvd](https://github.com/google/android-cuttlefish/tree/main/container/src/podcvd)
  and its
  [container run flags](https://github.com/google/android-cuttlefish/blob/main/container/src/podcvd/internal/container.go),
  [podcvd skill](https://github.com/google/android-cuttlefish/blob/main/skills/podcvd/SKILL.md))
- [google/cloud-android-orchestration](https://github.com/google/cloud-android-orchestration) —
  [`instances.Manager`](https://github.com/google/cloud-android-orchestration/blob/main/pkg/app/instances/instances.go),
  [Docker backend](https://github.com/google/cloud-android-orchestration/blob/main/pkg/app/instances/docker.go)
  (container requirements),
  [on-premises guide](https://github.com/google/cloud-android-orchestration/blob/main/scripts/on-premises/single-server/README.md)
- [AOSP: Cuttlefish on-premises](https://source.android.com/docs/devices/cuttlefish/on-premises)
- [KEP-753: Sidecar containers](https://github.com/kubernetes/enhancements/issues/753)
- [Android Studio for Platform](https://developer.android.com/studio/platform) —
  IDE target for native device integration (built-in Cuttlefish webview)
- [Cuttlefish WebRTC streaming](https://source.android.com/docs/devices/cuttlefish/webrtc) —
  local operator UI contract (`https://localhost:8443`)
- [jumpstarter PR #1033](https://github.com/jumpstarter-dev/jumpstarter/pull/1033) —
  `j adb attach`: leased devices join the client-owned ADB server
- `jumpstarter-driver-cuttlefish`
  (`python/packages/jumpstarter-driver-cuttlefish/`) — existing driver
- `controller/internal/exporterset/provisioner.go` — `Provisioner`
  interface; `provisioners/qemu/` — reference implementation

---

*This JEP is licensed under the
[Apache License, Version 2.0](https://www.apache.org/licenses/LICENSE-2.0),
consistent with the Jumpstarter project.*
