# JEP-0017: Multi-Exporter Leases and Inter-Exporter Port Forwarding

| Field             | Value                                                    |
| ----------------- | -------------------------------------------------------- |
| **JEP**           | 0017                                                     |
| **Title**         | Multi-Exporter Leases and Inter-Exporter Port Forwarding |
| **Author(s)**     | @kirkbrauer (Kirk Brauer)                                |
| **Status**        | Draft                                                    |
| **Type**          | Standards Track                                          |
| **Created**       | 2026-09-01                                               |
| **Updated**       | 2026-09-01                                               |
| **Discussion**    | *TBD (PR link)*                                          |
| **Requires**      | JEP-0014                                                 |
| **Supersedes**    |                                                          |
| **Superseded-By** |                                                          |

---

## Abstract

This JEP extends the existing `Lease` to bind more than one exporter, and adds
automatic port forwarding between those exporters so devices in one lease can
talk to each other. A lease gains an optional `spec.members[]` — each member a
role name (`phone`, `headunit`) plus the same selector fields a single-exporter
lease already uses — and an optional `spec.forwards[]` joining a **named port**
on one member to a named port on another. Ports are declared by drivers and
carried in the exporter's existing report, so a lease references
`headunit.rootcanal → phone.controller` and never learns an address; the
exporter resolves that locally. Because every member's exporter claim lives in
one object's status, binding a bench is a single atomic write: a lease holds
all of its devices or none. The data plane is the existing
`TcpPortforwardAdapter` with one substitution — a router peer stream in place
of a client stream — so `RouterService` and every driver work unchanged. This
realizes JEP-0014's deferred "composite leases — multiple exporters linked into
one logical lease" literally, and takes option 1 of JEP-0016's DD-8. The
worked example throughout is phone projection — a phone and a head unit
pairing over Bluetooth, then handing off to Wi-Fi — but nothing in the
mechanism is radio- or platform-specific: two ECUs joined over CAN, a BLE
peripheral and its gateway, or a plain serial cross-over are the same
declaration with different port names.

## Motivation

Jumpstarter's lease is the unit of exclusive access to *one* exporter.
`LeaseSpec` carries a single `selector` or a single `exporterRef`, and
`LeaseStatus` records a single `status.exporterRef`. Every layer above
inherits that shape: `RequestLease`/`Dial` are keyed by one lease,
`jmp shell` exports one `JUMPSTARTER_HOST`, and `JumpstarterTest` acquires
"a lease for a single exporter using the selector annotation".

That is the right primitive for the majority of HiL work — one board, one
harness — but an entire class of tests is about *interaction between
devices*, and today Jumpstarter cannot express it.

### The concrete problem: devices that must talk to each other

A large class of tests is not about a device but about an *interaction*
between two of them. The shape recurs across domains:

| Domain | Bench | What is exercised |
| --- | --- | --- |
| Phone projection | Phone + head unit | Pairing, then session handover to a high-bandwidth link |
| Wireless peripherals | Peripheral + host, or peripheral + gateway | Advertising, pairing, reconnection, roaming |
| Automotive networks | Two ECUs, or ECU + gateway | Bus arbitration, routing, diagnostics across a segment |
| Device-to-device apps | Two handsets | Discovery, transfer, sync over Bluetooth or Wi-Fi Direct |
| Serial / console harnesses | DUT + companion | Protocol conformance over a cross-over link |

Every one of these needs the same two things Jumpstarter cannot give: **two
devices held at once**, and **a path between them**.

**Phone projection is the worked example** used throughout this JEP, because
it exercises the hardest version of both requirements. It is a two-device
protocol by construction: the phone and the head unit first pair over
**Bluetooth**, then the head unit hands the session off to a peer-to-peer
**Wi-Fi** link, because Bluetooth lacks the bandwidth for continuous video.
Validating it means holding both devices *simultaneously*, keeping them
connected, driving both, and asserting on both — a phone-only or
head-unit-only lease cannot observe the handover at all. The same structure
holds for Android Auto and for Apple CarPlay, and for a head unit running
Android, Linux, or QNX; the pairing-then-handover pattern is a property of
projection, not of one vendor's stack.

Two capabilities are missing from existing tooling, and projection tests need
both: **heterogeneous benches** — a Linux or QNX head unit alongside an
Android phone — and **pairing two virtual devices to each other**.

Running that on Jumpstarter today requires the test author to hand-roll
everything the lease layer should provide:

- **No atomic acquisition.** The client requests two independent leases. If
  the second selector is unsatisfiable the first is already held, so the
  test either blocks holding scarce hardware or unwinds by hand. Under
  contention, two concurrent benches can each take one half of the pair and
  deadlock until expiry — the classic gang-scheduling failure.
- **No shared lifetime.** Two leases expire independently. Half a bench
  disappearing mid-run produces a failure that looks like a device fault.
- **No correlation for policy or observability.** `ExporterAccessPolicy`
  sees two unrelated requests; JEP-0013 traces see two unrelated leases.
  Nothing records that these devices were *a bench*.
- **No path between the devices.** Even with both leases in hand, the two
  exporters have no way to exchange device-level traffic. Every Jumpstarter
  stream is client↔exporter; there is no exporter↔exporter path.

The last point is the hard one, and it is what makes this more than an
ergonomics change.

### The virtual case: what nobody has shipped yet

For virtual devices the connectivity problem is sharper. Cuttlefish and the
Android emulator can already pair virtual devices to each other — but only
within one host:

- Cuttlefish shares its virtual radio media between instances launched from
  a single `launch_cvd --num_instances=n`, or from separate `launch_cvd`
  invocations that are pointed at the *same* `--vhost_user_mac80211_hwsim`
  socket path and the same rootcanal/netsim daemon. Both mechanisms are
  host-local Unix sockets and loopback TCP ports.
- The Android emulator's new networking backplane (36.5) is explicitly
  described as bridging "all running instances on the same host machine".
- `podcvd`, Google's container wrapper, publishes each container's ports on
  its own IP but keeps radio simulation inside the container group.

So the state of the art for virtual multi-device Android testing is: *both
devices must live on one machine*. That constraint is exactly what a
cluster-scheduled, autoscaled pool of one-CVD-per-Pod exporters (JEP-0016)
breaks — and it is why JEP-0016's DD-8 deferred multi-CVD groups pending
"a cross-Pod virtual-radio story… real upstream-facing work."

The encouraging part is that these simulators are reached over ordinary
sockets. `rootcanal`, the virtual Bluetooth controller, accepts HCI on a TCP
port — which is why `jumpstarter-driver-bt-peer` can already attach a
`bumble` peer to a CVD with `transport: "tcp-client:127.0.0.1:7300"`.
`netsim`, the Rust simulator that orchestrates Bluetooth, BLE, Wi-Fi and UWB
for both Cuttlefish and the emulator, accepts virtual chips over a
bidirectional gRPC stream (`PacketStreamer.StreamPackets`, whose first
`PacketRequest` carries a `ChipInfo` naming the device and chip kind).
`wmediumd` speaks over a frame socket. Every one of them is something a
process connects to. Nothing about "same host" is fundamental; it is an
artifact of the fact that nobody has forwarded those sockets between
machines under a common lease.

### Prior art's ceiling, precisely

Google's OmniLab Android Test Station is the closest existing system, and
being exact about what it can and cannot do matters, because the naive
version of this comparison is wrong.

ATS 2.0 runs on Mobile Harness (open-sourced as `google/device-infra`), and
it **does** do multi-device. Ad-hoc testbeds gang-schedule N devices for one
job: `AdhocTestbedSchedulingUtil.findSubDevicesSupportingJob` performs
maximum-cardinality bipartite matching of `SubDeviceSpec`s against idle
devices, and `TestbedDevice`, `CompositeDevice`, and
`SubDeviceSynchronizationDriver` all exist. It also has a multi-host mode.
So "ATS cannot do multi-device" and "ATS is single-host" are both too strong.

The actual constraint is one check in
`infra/controller/scheduler/simple/SimpleScheduler.java`. In `allocate`,
every device locator in an allocation must share a single `LabLocator`;
otherwise the allocation is refused with *"Lab locators do not match. Can not
create allocation"*. **A multi-device job is therefore confined to one lab
host.** Multi-host mode is one controller with N workers for fleet-level
pooling — it does not make a single job span hosts. And in either case there
is no bridged medium between roles: Tradefed states its own version of the
gap verbatim — *"No APIs yet exist to conduct operations from one device to
another, such as `device1.sync(device2)`."*

Two further limits shape the opportunity. Mobile Harness's `Device`
abstraction is ADB-shaped and runs inside the lab-server JVM next to the
device, and its open-source platform coverage is `platform/android`,
`platform/androiddesktop`, and `platform/testbed` — there is no Linux, QNX,
serial, power, or CAN support, and extending it means in-tree Java and Bazel
rather than an out-of-tree package.

This JEP targets exactly that seam:

- **Physical ↔ physical**: a phone and a head unit on two exporters, on
  **different lab hosts**, in one lease — the allocation `SimpleScheduler`
  refuses.
- **Virtual ↔ virtual**: two CVDs in two Pods, on two nodes, pairing over
  Bluetooth and Wi-Fi — the thing that has not been done anywhere.
- **Physical ↔ virtual (hybrid)**: a real phone in a lab rack paired to a
  virtual head unit running in the cluster, via a gateway exporter that owns
  a real radio.

The last one is the interesting business case: labs have scarce physical
head units and abundant phones (or the reverse), and virtualizing the
abundant half while keeping the scarce half real is a direct cost and
throughput win no host-local scheduler can offer.

### User Stories

- **As a** phone-projection QA engineer, **I want to** lease a phone and a
  head unit as one bench with roles, **so that** my test either gets both
  devices or waits — never half a bench, never a deadlock against a
  concurrent run.
- **As an** automotive platform developer, **I want to** pair two *virtual*
  devices running in different cluster Pods over Bluetooth and then hand off
  to Wi-Fi, **so that** I can validate wireless projection in CI without a
  physical lab or a single fat host.
- **As a** lab operator whose devices are in different racks or different
  buildings, **I want** one lease to span them, **so that** my bench is not
  limited to devices plugged into the same machine.
- **As a** test author with a mixed bench, **I want** a QNX or Linux head
  unit alongside an Android phone, **so that** device type is a driver
  choice rather than a platform limit.
- **As an** embedded engineer testing a CAN gateway, **I want** two ECU
  exporters joined over a forwarded bus, **so that** I can exercise routing
  between segments without physically cabling them to one machine.
- **As a** BLE peripheral developer, **I want** my DUT leased alongside a
  central acting as its phone, **so that** pairing and reconnection are
  covered in CI rather than by hand at a desk.
- **As a** Jumpstarter user who already knows leases, **I want** a bench to
  be *a lease*, **so that** everything I know about `jmp create lease`,
  expiry, release, and access policy carries over without learning a second
  resource.

## Proposal

Three additions, none of them a new resource kind:

- **Members.** A lease gains optional `spec.members[]`, each a role name plus
  the same `selector` / `exporterRef` fields a single-exporter lease already
  uses. A lease with members binds every one of them or none.
- **Ports.** A driver may declare named ports it **provides** (a service
  listening locally) or **requires** (a socket it will dial). Ports travel in
  the exporter's existing report; addresses never leave the exporter.
- **Forwards.** A lease gains optional `spec.forwards[]`, each joining a
  provided port on one member to a required port on another. The exporters
  establish the forward themselves over the existing router.

A lease with members is called a **bench** informally, but it is not a new
kind of object: it is a `Lease`, it appears in `jmp get leases`, it expires
the way leases expire, and `spec.release` ends it.

### Ports

A port is a named connection point on a driver. There are exactly two
directions, and the direction is what makes a forward well-formed.

| Direction | Meaning | Example |
| --- | --- | --- |
| `provides` | A service is listening; something may be forwarded *from* it | `rootcanal` on a Cuttlefish exporter — HCI on `127.0.0.1:7300` |
| `requires` | The driver will dial a local address; a forward may be delivered *to* it | `controller` on a `bt-peer` exporter — where its bumble stack expects an HCI controller |

`provides` needs almost nothing new, because a `TcpNetwork` child already
*is* a named provided port — the child's name is the port name:

```yaml
export:
  cuttlefish:
    type: jumpstarter_driver_cuttlefish.driver.Cuttlefish
    children:
      rootcanal:
        type: jumpstarter_driver_network.driver.TcpNetwork
        config: { host: 127.0.0.1, port: 7300 }
        ports:
          - name: rootcanal
            direction: provides
            protocol: hci-h4        # optional
```

`requires` is the genuinely new concept: a declaration that the driver will
dial a local address, and that the exporter should bind an inbound forward
there.

```yaml
export:
  bt_peer:
    type: jumpstarter_driver_bt_peer.driver.BtPeer
    config:
      transport: "tcp-client:127.0.0.1:7300"   # unchanged
    ports:
      - name: controller
        direction: requires
        listen: 127.0.0.1:7300                 # local; never reported
        protocol: hci-h4                       # optional
```

Note what did *not* change: `bt-peer`'s Python is untouched, and its
`transport` string still points at `127.0.0.1:7300`. The lease simply makes
that address resolve to a rootcanal in another Pod on another node. A driver
that can talk to a local service can now talk to a remote one without
knowing the difference.

### Declaring a bench

```yaml
apiVersion: jumpstarter.dev/v1alpha1
kind: Lease
metadata:
  name: android-auto-bench
  namespace: jumpstarter-lab
spec:
  clientRef:
    name: ci-runner
  duration: 45m
  members:
    - name: phone
      selector:
        matchLabels:
          device-type: android-phone
          android-version: "15"
    - name: headunit
      selector:
        matchLabels:
          device-type: aaos-headunit
  forwards:
    - name: bt
      from: { member: headunit, port: rootcanal }   # provides
      to:   { member: phone,    port: controller }  # requires
```

No addresses, no ports numbers, no medium taxonomy. The lease names roles and
port names; both sides' internals stay inside their exporters (DD-6).

`members[].selector` and `members[].exporterRef` are the *same* fields as
the top-level `spec.selector` and `spec.exporterRef` — a member is the
existing lease request with a role name attached. The single-exporter form is
unchanged and exactly equivalent to a one-member lease, so today's leases are
already the degenerate case rather than a legacy shape to migrate (DD-2).

A member may be marked `optional: true`, in which case the lease binds
without it and the role resolves to `None` on the client.

#### A bench with no radios in it

Nothing above is projection-specific. The same two fields express two ECUs
sharing a CAN segment, where one exporter fronts the bus over TCP (via
`socketcand` or an equivalent bridge) and the other dials it:

```yaml
spec:
  members:
    - name: gateway
      selector: { matchLabels: { ecu-role: gateway } }
    - name: node
      selector: { matchLabels: { ecu-role: body-controller } }
  forwards:
    - name: powertrain-bus
      from: { member: gateway, port: can0 }    # provides
      to:   { member: node,    port: can }     # requires
```

Only the port names differ. The controller applies the same validation —
both ports exist, `provides` meets `requires`, declared protocols agree — and
the same forward machinery carries the bytes. A serial cross-over between a
DUT and a companion board is the same shape again.

### Acquiring and using a bench

The existing commands take members and forwards; there is no parallel
command set.

```console
$ jmp create lease \
    --member phone=device-type=android-phone \
    --member headunit=device-type=aaos-headunit \
    --forward bt=headunit.rootcanal:phone.controller \
    --duration 45m
android-auto-bench

$ jmp get lease android-auto-bench
NAME                 ENDED   CLIENT      EXPORTER                       AGE
android-auto-bench   false   ci-runner   phone=rack3-pixel8,            12s
                                         headunit=cf-auto-7b2c
```

Port names are discoverable rather than tribal knowledge, which is the point
of putting them in the report (DD-7):

```console
$ jmp get exporter cf-auto-7b2c -o json | jq '.status.devices[].ports'
[{"name":"rootcanal","direction":"provides","protocol":"hci-h4"}]
```

In a shell, roles become top-level names alongside the usual driver clients:

```console
$ jmp shell --lease android-auto-bench
jumpstarter ⚡ android-auto-bench ➤ j phone adb shell getprop ro.product.model
Pixel 8
jumpstarter ⚡ android-auto-bench ➤ j headunit power on
jumpstarter ⚡ android-auto-bench ➤ j forward status
NAME   FROM                  TO                  MODE     STATE       A→B       B→A
bt     headunit.rootcanal    phone.controller    router   connected   1.2 MiB   0.9 MiB
```

In Python, the existing `lease()` context manager grows `members` and
`forwards`, and a multi-member lease yields a mapping of roles to the same
client objects a single-exporter lease yields directly:

```python
from jumpstarter.config.client import ClientConfigV1Alpha1

config = ClientConfigV1Alpha1.load("default")

with config.lease(
    members={"phone": "device-type=android-phone", "headunit": "device-type=aaos-headunit"},
    forwards=[Forward("bt", frm=("headunit", "rootcanal"), to=("phone", "controller"))],
    duration=timedelta(minutes=45),
) as lease:
    with lease.connect() as bench:
        phone = bench.phone
        headunit = bench.headunit

        headunit.power.on()
        phone.adb.wait_for_device()

        # The forward is already up; drive the protocol, not the plumbing.
        bench.forwards["bt"].wait_connected(timeout=30)
        phone.bt_peer.start({"name": "Bumble-Phone"})
        phone.bt_peer.wait_connection(timeout=60)

        assert phone.adb.shell("dumpsys activity | grep -c CarProjection") == "1"
```

`config.lease(selector=...)` without `members` behaves exactly as it does
today and yields a single client — the one-member case is not special-cased
in the API, only in what `connect()` returns (DD-2).

`JumpstarterTest` grows `members` and `forwards` class variables next to
`selector`, so existing pytest suites extend without a second base class.

### Running existing multi-device suites

Because a bench is a set of roles with ADB-capable members, it can be
projected into the config formats existing Android multi-device tests already
consume:

```console
$ jmp get lease android-auto-bench -o mobly > testbed.yml
$ mobly_test.py -c testbed.yml --test_bed android-auto-bench
```

which emits a Mobly testbed whose `AndroidDevice` controllers point at the
per-member ADB endpoints Jumpstarter has forwarded locally, with the role
name carried through as the Mobly device label. A Mobile Harness
`SubDeviceSpec` mapping follows the same shape. This is the integration seam
with ATS/OmniLab: the tests and the results pipeline do not change, the
*bench* changes from "devices sharing one `LabLocator`" to "any mix of
physical and virtual devices anywhere the controller can reach."

### How a forward comes up

The data plane already exists. `adapters/portforward.py` implements
`TcpPortforwardAdapter` as `TemporaryTcpListener` + `client.stream_async()` +
`forward_stream()`. The inter-exporter version is the same three primitives
with one substitution — a router peer stream in place of the client stream:

1. Once the lease is bound, the controller validates both ports against the
   exporters' reports and instructs each exporter over its existing `Listen`
   stream.
2. Each exporter calls the new `DialPeer` RPC and receives a router endpoint
   plus a token whose `stream` claim is the **same** for both ends.
3. Both call `RouterService.Stream`. The router already "connects caller to
   another caller of the same stream" — a symmetric rendezvous that needs no
   protocol change to pair two exporters instead of a client and an exporter
   (DD-5).
4. The `provides` side dials its local service (`127.0.0.1:7300`) and splices
   it to the stream with `forward_stream`. The `requires` side opens a
   `TemporaryTcpListener` on its declared `listen` address and splices each
   accepted connection to the stream.
5. **Fast path**: if `DialPeer` returned a peer hint, each exporter races a
   direct dial against the router path and keeps whichever completes first
   (DD-4).

```text
       ┌──────── Lease: android-auto-bench (one object) ─────────┐
       │  status.members:                                        │
       │    phone    → Exporter/rack3-pixel8                     │
       │    headunit → Exporter/cf-auto-7b2c                     │
       └───────┬─────────────────────────────────┬───────────────┘
               │                                 │
  ┌────────────▼────────────┐       ┌────────────▼────────────┐
  │ Exporter: rack3-pixel8  │       │ Exporter: cf-auto-7b2c  │
  │  bt_peer                │       │  cuttlefish             │
  │   requires: controller  │       │   provides: rootcanal   │
  │   listener 127.0.0.1:7300│      │   dials 127.0.0.1:7300  │
  └────────────┬────────────┘       └────────────┬────────────┘
               │                                 │
               │      ┌───────────────────┐      │
               └─────►│  RouterService    │◄─────┘   (default)
                      │  same stream id   │
                      └───────────────────┘
               └─────────  direct peer  ─────────┘   (fast path)
```

The client is not in the data path.

### Attaching media, simulated and physical

With forwards as the mechanism, "bridging a medium" stops being a special
subsystem and becomes a question of which port each stack exposes. Radios are
the demanding case, but nothing in the table below is privileged — a CAN bus
or a serial cross-over is the same declaration:

| Stack | Port | Direction | Notes |
| --- | --- | --- | --- |
| rootcanal (Cuttlefish, emulator) | `rootcanal` | provides | HCI on TCP; hosts attach to it |
| `bt-peer` (bumble) | `controller` | requires | Dials an HCI controller |
| netsim | `netsim` | provides | gRPC `PacketStreamer`; attaching side must originate `ChipInfo` |
| `wmediumd` / `mac80211_hwsim` | `hwsim` | provides | Frame socket |
| Gateway exporter (real adapter) | `hci` | provides | A real radio, presented as HCI |
| `socketcand` / CAN-over-TCP bridge | `can` | provides | A CAN segment reachable as a socket |
| Serial bridge (pty or TCP) | `console` | requires/provides | Cross-over between a DUT and a companion |

Two consequences worth stating plainly, both of which generalize beyond
radios. First, HCI is **asymmetric**: a host
attaches to a controller. Forwarding one rootcanal's port into another
rootcanal wires controller to controller and nothing happens — which is
precisely why forwards are directional and why a `provides → provides`
forward is rejected (DD-6). Second, netsim's port is not a transparent
splice: the attaching side must speak `StreamPackets` and send `ChipInfo`
first, so the consumer is a protocol-terminating driver rather than a raw
socket. Whether two CVDs can be joined at rootcanal directly, or whether
netsim must sit in the middle because it is the component that presents a
controller to multiple hosts, is an open question the Phase 1 prototype
answers (see Unresolved Questions).

For **physical ↔ virtual** there is no way to relay a real phone's internal
HCI: the radio is inside the device. The bridge happens in RF, at a
**gateway exporter** owning a real adapter and physically near the physical
device (DD-11). This is a lab-hardware requirement, not a software trick.

### API / Protocol Changes

All changes are **additive fields on existing types**, plus one new RPC. No
new CRD, no new API group, no existing field changes meaning.

**Driver report** — ports are optional, and an exporter that reports none
simply cannot participate in forwards (DD-7):

```protobuf
message DriverInstanceReport {
  // ... fields 1-5 unchanged ...
  repeated PortReport ports = 6;   // NEW, optional
}

message PortReport {
  string name = 1;                 // "rootcanal", "controller"
  PortDirection direction = 2;     // PROVIDES | REQUIRES
  optional string protocol = 3;    // free-form; compared only if both ends set it
}

enum PortDirection {
  PORT_DIRECTION_UNSPECIFIED = 0;
  PORT_DIRECTION_PROVIDES = 1;
  PORT_DIRECTION_REQUIRES = 2;
}
```

The `listen` address of a `requires` port is deliberately **absent**: it is
local to the exporter and no other component needs it (DD-6).

**`LeaseSpec`** gains two optional lists:

```go
type LeaseSpec struct {
    // ... all existing fields unchanged ...

    // Members of a multi-exporter lease. When empty, the lease binds a
    // single exporter using Selector/ExporterRef exactly as before.
    // +kubebuilder:validation:MaxItems=8
    Members []LeaseMember `json:"members,omitempty"`

    // Port forwards between members. Requires Members.
    Forwards []LeaseForward `json:"forwards,omitempty"`
}

type LeaseMember struct {
    Name          string                       `json:"name"`
    Selector      metav1.LabelSelector         `json:"selector,omitempty"`
    ExporterRef   *corev1.LocalObjectReference `json:"exporterRef,omitempty"`
    Optional      bool                         `json:"optional,omitempty"`
    AllowDisabled bool                         `json:"allowDisabled,omitempty"`
}

type LeaseForward struct {
    Name string          `json:"name"`
    From ForwardEndpoint `json:"from"`   // must resolve to a `provides` port
    To   ForwardEndpoint `json:"to"`     // must resolve to a `requires` port
    // Auto (default) | Router | Direct | ClientRelay
    Mode string `json:"mode,omitempty"`
}

type ForwardEndpoint struct {
    Member string `json:"member"`
    Port   string `json:"port"`
}
```

**`LeaseStatus`** gains parallel lists and keeps its scalar:

```go
type LeaseStatus struct {
    // ... all existing fields unchanged ...
    // ExporterRef stays authoritative for single-exporter leases and is
    // left nil for multi-member leases (DD-2).

    Members  []LeaseMemberStatus  `json:"members,omitempty"`
    Forwards []LeaseForwardStatus `json:"forwards,omitempty"`
}

type LeaseMemberStatus struct {
    Name        string                       `json:"name"`
    ExporterRef *corev1.LocalObjectReference `json:"exporterRef,omitempty"`
    Priority    int                          `json:"priority,omitempty"`
    SpotAccess  bool                         `json:"spotAccess,omitempty"`
}

type LeaseForwardStatus struct {
    Name    string `json:"name"`
    State   string `json:"state"`             // Pending|Connecting|Connected|Reconnecting|Failed
    Mode    string `json:"mode,omitempty"`    // transport actually in use
    Message string `json:"message,omitempty"`
}
```

`ExporterStatus.Devices[]` gains the reported ports so the controller can
validate forwards against bound exporters.

The existing CEL rules are extended, not replaced — the current
"one of selector or exporterRef is required" rule gains a `members` arm, plus
new rules for mutual exclusion, unique role names, forwards referencing
declared members, and member immutability (mirroring `tags` and `context`).

**Protocol** — three additive fields and one new RPC:

```protobuf
message RequestLeaseRequest {
  google.protobuf.Duration duration = 1;  // unchanged
  LabelSelector selector = 2;             // unchanged
  repeated LeaseMember members = 3;       // NEW
  repeated LeaseForward forwards = 4;     // NEW
}

message GetLeaseResponse {
  // ... fields 1-6 unchanged; exporter_uuid set only when single-member ...
  repeated LeaseMemberStatus members = 7;   // NEW
  repeated LeaseForwardStatus forwards = 8; // NEW
}

message DialRequest {
  string lease_name = 1;                // unchanged
  optional string member_name = 2;      // NEW: required for multi-member leases
}

service ControllerService {
  // ... existing RPCs unchanged ...
  rpc DialPeer(DialPeerRequest) returns (DialPeerResponse);
}

message DialPeerRequest {
  string lease_name = 1;
  string forward_name = 2;
  string member_name = 3;
}

message DialPeerResponse {
  string router_endpoint = 1;
  string router_token = 2;              // `stream` claim shared by both ends
  optional string peer_endpoint = 3;    // fast-path hint
  optional string peer_token = 4;       // authenticates a direct dial
}
```

`ReleaseLeaseRequest`, `ListLeasesRequest`, `RouterService`, and
`router.proto` are untouched.

**CLI surface** — existing commands, new flags:

- `jmp create lease --member role=selector --forward name=m.port:m.port`
- `jmp get lease[s]` prints per-role exporters; `-o json|yaml|name` unchanged
- `jmp get lease <name> -o mobly`
- `jmp get exporter <name>` shows declared ports
- `jmp shell --lease <name>`, `j <role> <driver> ...`, `j forward status`
- `jmp delete lease` / `jmp update lease` need no changes

### Hardware Considerations

- **Gateway exporters** for hybrid benches need a real Bluetooth adapter (a
  USB HCI dongle is sufficient — `bumble` already supports `usb:0`) and, for
  Wi-Fi, a 5 GHz-capable radio in the same RF space as the physical device.
  This is new lab hardware, and the JEP does not pretend otherwise.
- **RF isolation.** Multiple physical benches in one room share the air. Labs
  running more than one need shielded enclosures or channel planning; the
  controller cannot schedule around collisions it cannot observe. Express RF
  domains as exporter labels and let selectors keep benches apart.
- **Physical proximity is a scheduling constraint** for physical ↔ physical
  radio benches, modeled with ordinary labels (`rf-domain: rack-3`), not new
  machinery.
- **Latency budgets.** Bluetooth HCI is timing-sensitive: supervision
  timeouts are seconds, but L2CAP/HCI flow control and A2DP jitter buffers
  are far tighter. A router-relayed forward adds two gRPC hops; measuring
  that budget is an acceptance criterion, and it is the main reason the
  direct fast path exists.
- **Wi-Fi frame forwarding is the most latency-sensitive path.** `wmediumd`
  models RSSI-based delivery and expects medium-like timing; a TCP substrate
  introduces head-of-line blocking a real air interface does not have (DD-10).
- **Listener collisions.** A `requires` port binds a fixed local address. In
  a Pod that is free; on a physical lab host already running its own
  rootcanal it is not. This is a real deployment constraint, called out in
  Unresolved Questions.
- **Degraded hardware.** If a member's exporter goes offline mid-lease, the
  lease reports `Ready=False` naming the role and does *not* silently
  continue with a partial bench (DD-3).

## Design Decisions

### DD-1: Multi-exporter representation — extend `Lease` vs. a new CR

**Alternatives considered:**

1. **Extend `Lease`** with `spec.members[]` / `status.members[]`; a
   single-exporter lease is the one-member degenerate case.
2. **A new `LeaseGroup` CR owning N child `Lease` CRs.**
3. **Client-side coordination only** — the client acquires N leases and
   correlates them by tag.

**Decision:** Option 1 — extend `Lease`.

**Rationale:** The deciding factor is how exporter exclusivity is actually
implemented. In `lease_controller.go`, an exporter is claimed by writing
`lease.Status.ExporterRef`, and exclusivity is enforced by scanning other
active leases for a claim on the same exporter
(`ListActiveLeases` → `attachExistingLeases` → `filterOutLeasedExporters`).
The claim is persisted by a **single** `r.Status().Update(ctx, &lease)`.

All of a lease's exporter claims therefore live in one object's status, and
binding N of them is one write. Either every member's claim lands or none
does. **Partial acquisition is structurally impossible**, so there is nothing
to time out, nothing to release-and-retry, and no gang scheduler.

Option 2 gets the opposite property. With N child leases, the existing
reconciler binds each independently with its own `Status().Update`, so "three
of four members bound" is a real, durable state in etcd. Everything needed to
recover from it — admission gating, an acquisition timeout,
release-all-on-timeout, jittered backoff, and a contention test to
demonstrate no deadlock — exists purely to clean up after a state option 1
never enters. That is a large amount of new machinery in the most
correctness-critical controller in the project, in exchange for a separation
that buys little: JEP-0014 already described the goal as "multiple exporters
linked into **one logical lease**."

Option 2's genuine advantage is that it does not touch `Lease`. That matters
less than it appears, because the compatibility risk concentrates in exactly
one field, `status.exporterRef`, which DD-2 handles directly. Option 2 also
does not avoid protocol work: `Dial(lease_name)` has no way to say *which*
device, so a member selector must be added either way.

Option 3 provides no atomicity, no co-scheduling, no deadlock avoidance, and
leaves policy unable to reason about a bench — most of the motivation.

**One thing this decision does not fix.** Atomic means no partial *holds*; it
does not mean two leases can never race for the same exporter. The
exclusivity scan reads a possibly-stale cache, and two leases racing for one
exporter write to *different* objects, so optimistic concurrency does not
catch it. That race exists today for single-exporter leases and is already
acknowledged in the code (*"we could have multiple clients trying to lease
the same exporters… we will need to construct a lease scheduler with the view
of all leases and exporters"*). This JEP neither worsens nor fixes it. A
multi-member lease does fit that future scheduler better than N correlated
objects would: one lease is one scheduling unit, the way one Pod is.

### DD-2: Keep `status.exporterRef` scalar; add `status.members[]` alongside

**Alternatives considered:**

1. **Keep the scalar, add a parallel list.** `status.exporterRef` stays
   authoritative for single-exporter leases and is left **nil** for
   multi-member leases, which populate `status.members[]` instead.
2. **Promote the scalar to a list** and migrate every reader.
3. **Always populate both**, setting `status.exporterRef` to the first member.

**Decision:** Option 1.

**Rationale:** This is the whole compatibility story of DD-1. Every existing
reader of `status.exporterRef` — the `Dial` path, the `Exporter` print
column, JEP-0013 telemetry, and the JEP-0016 Host Orchestrator façade
(explicitly *a view over ordinary Lease CRs*) — keeps working
**byte-identically** for single-exporter leases, which is every lease that
exists today.

For multi-member leases, option 1 makes old readers see a nil
`status.exporterRef`, which they already interpret as "not bound yet". That
is a **fail-safe** degradation: an unaware consumer shows an unbound lease
rather than confidently operating on one arbitrary device out of several.
Option 3 is the fail-dangerous version — the façade would present a
two-device bench as a single host and route Host Orchestrator calls to
whichever member sorted first. Option 2 is honest but forces a migration on
every consumer for a feature most will never see.

The same rule applies on the wire: `GetLeaseResponse.exporter_uuid` stays set
only for single-member leases. In the client library, `lease.connect()`
returns a driver client directly for a single-member lease and a role mapping
for a multi-member one — a difference the caller opted into by passing
`members`.

`Dial` is the one place where silence is unacceptable, because a multi-member
lease has no defensible default device. `DialRequest` gains an optional
`member_name`; calling `Dial` on a multi-member lease without it returns
`INVALID_ARGUMENT` naming the available roles rather than guessing.

### DD-3: Partial-bench behavior when a member is lost mid-lease

**Alternatives considered:**

1. **Fail the lease** — set `Ready=False`, name the failed role, keep the
   surviving members held until release or expiry.
2. **End the whole lease immediately** on any member loss.
3. **Continue silently** with the surviving members.

**Decision:** Option 1.

**Rationale:** Option 3 is how a two-device test turns into a confusing
one-device pass; it is never right. Between 1 and 2, keeping the survivors
held respects the test: a client that has just lost its head unit usually
wants to collect logs and artifacts from the phone before releasing, and an
abrupt teardown destroys the evidence needed to diagnose the failure. The
condition is loud, and the client library raises on the next call into the
failed role rather than returning stale data.

This concerns a member lost *after* binding — distinct from acquisition,
which DD-1 makes all-or-nothing.

### DD-4: Forward transport — router peer streams vs. client relay vs. pure P2P

**Alternatives considered:**

1. **Router peer streams with an optional direct P2P fast path.**
2. **Client-relayed** — the client pumps bytes between two streams.
3. **Direct peer-to-peer only.**

**Decision:** Option 1.

**Rationale:** Option 2 needs *zero* protocol change and could be prototyped
immediately — but it doubles RTT on a latency-sensitive path, routes lab
traffic through whatever machine ran the test, and breaks for headless CI and
sustained throughput. It is retained as an explicit `mode: client-relay`
debug transport, not as the architecture.

Option 3 has the best data plane and fails at exactly the case this JEP is
for: the hybrid bench, where a lab exporter behind NAT and a cluster Pod are
not mutually routable. Jumpstarter's value in that topology is that the
controller and router are the only things both sides must reach.

Option 1 keeps one authentication model — a forward is authorized by the
lease that names it, exactly as a stream is authorized by the lease that
names it — while permitting the fast data plane where it is available. The
fast path is an *optimization*, not a requirement: a bench that works over
the router works everywhere, and `mode: router` makes the slow path explicit
for tests that need reproducible timing.

### DD-5: Router changes — none

**Alternatives considered:**

1. **Reuse `RouterService.Stream` unchanged**, issuing both endpoints a token
   bearing the same `stream` claim.
2. **Add a peer-specific RPC** to `router.proto` with explicit A/B roles.

**Decision:** Option 1 — no `router.proto` change.

**Rationale:** `RouterService.Stream`'s documented contract is already
"Stream connects caller to another caller of the same stream", and its token
claims (`sub: jumpstarter client/exporter`, `stream: stream id`) already
accommodate an exporter as either party. The router is a symmetric rendezvous
that never needed to know which side was the client. A peer RPC would encode
an asymmetry that does not exist and fork the data path — two implementations
to keep correct, two places to fix flow-control bugs, and a second surface
for the datagram work in Future Possibilities. Everything peer-specific
belongs in token issuance, which is the controller's job.

### DD-6: Named ports with direction, not addresses

**Alternatives considered:**

1. **Named ports on both ends**, each declaring `provides` or `requires`;
   the lease references names only.
2. **Raw addresses in the lease** — `from: headunit.rootcanal`,
   `to: {member: phone, listen: 127.0.0.1:7300}`.
3. **Untyped named endpoints** — names on both ends but no direction.

**Decision:** Option 1.

**Rationale:** Option 2 forces the *client* to know the internal architecture
of an exporter it did not configure: that `bt-peer` expects its controller on
7300, that the address is on loopback, that nothing else is bound there. That
is exporter-private detail leaking into a lease manifest, and it breaks the
moment a lab reconfigures a port. With named ports the lease says
`headunit.rootcanal → phone.controller` and each exporter resolves its own
address locally.

Direction is what makes option 1 strictly better than option 3, and it took
three attempts to see why. A forward is inherently asymmetric — one side has
a listening service, the other gets a local listener — and that asymmetry is
exactly the relationship the underlying protocols have. HCI has a host side
and a controller side: `bt-peer` attaches to rootcanal *as a host*. Forward
one rootcanal's port into another rootcanal and you have wired controller to
controller, and nothing happens. A `provides → provides` forward is therefore
rejected structurally, with no knowledge of Bluetooth anywhere in the
controller.

Direction also gives the validation something real to check (DD-7), which
name-matching alone cannot.

### DD-7: Ports are an optional part of the exporter report

**Alternatives considered:**

1. **A new optional repeated `ports` field** on `DriverInstanceReport`,
   mirrored into `ExporterStatus.Devices[]`.
2. **Encode ports as driver-instance labels**, which already flow through to
   `ExporterStatus.Devices[].Labels` — zero proto and CRD change.
3. **No reporting** — ports live only in exporter config, and forwards fail
   at connect time if misconfigured.

**Decision:** Option 1, and optional in the strong sense: an exporter that
reports no ports simply cannot participate in forwards.

**Rationale:** Option 3 loses the two things that make named ports usable.
Without reporting, the controller cannot validate a forward before
establishing it, and — more importantly — a client has no way to *discover*
port names, which just relocates the tribal-knowledge problem DD-6 set out to
solve. Ports in the report make `jmp get exporter` the answer to "what can I
wire up here", the same way it already answers "what drivers are here". That
also sits naturally alongside JEP-0011's introspection direction rather than
inventing a parallel discovery channel.

Option 2 is genuinely tempting: `DriverInstanceReport.labels` already exists
and already reaches `ExporterStatus.Devices`, so ports could ship with no
schema change at all. It fails on the `provides`/`requires` asymmetry. A
`provides` port is already a driver instance — a `TcpNetwork` child with a
uuid in the report — so labelling it annotates something real. A `requires`
port **has no backing driver instance**; it is a declared need, so option 2
would require synthesizing a phantom report entry with no methods purely to
carry two labels, plus two parallel key namespaces (`port.jumpstarter.dev/*`
and `port-protocol.jumpstarter.dev/*`) to keep in sync.

Being optional is what makes this free: `repeated` defaults to empty, so
every existing exporter reports nothing new, no version negotiation is
needed, and an old exporter against a new controller is simply not a forward
endpoint — the correct answer anyway.

**What is deliberately not reported:** the `listen` address of a `requires`
port. It is local to the exporter and no other component needs it (DD-6).
Keeping it out means the report carries only label-safe scalars, and means a
lab can re-address a port without touching anything outside that exporter.

### DD-8: No protocol taxonomy; direction plus an optional tag

**Alternatives considered:**

1. **A `medium` enum** (`bluetooth | wifi | uwb | serial | can`) matched
   between endpoints.
2. **A `format`/wire-protocol token** matched for equality.
3. **Direction only**, with an optional free-form `protocol` compared solely
   when both ends declare it.

**Decision:** Option 3.

**Rationale:** Option 1 validates the wrong axis. `medium: bluetooth` would
approve a forward between a raw-HCI rootcanal port and a netsim
`PacketStreamer` port — both are "bluetooth", neither can talk to the other,
because one is raw bytes and the other is length-delimited protobuf behind a
`ChipInfo` handshake. It describes what traffic *represents*, not what the
bytes *are*, so it approves the one pairing that most needs catching.

Option 2 fixes the axis but is still insufficient on its own: `hci-h4` on
both ends is exactly the controller-to-controller wiring DD-6 rejects. The
thing that had to be checked was never the payload format but the
*relationship*, and once direction is modelled, most of the value is already
captured without any taxonomy to define, version, or argue about.

An optional `protocol` string remains useful for the residue — it catches
wiring a `vnc` provides-port into an HCI requires-port — but comparing it
only when both ends declare it keeps it opt-in. Driver authors who want the
check get it; nobody is forced to classify anything, and there is no closed
enum to maintain.

The honest cost: a forward whose two ends speak different protocols and
declare no `protocol` tag will connect and then fail at the first byte. This
is the same guarantee `kubectl port-forward` gives, which nobody treats as a
defect, but it is a deliberate retreat from admission-time protocol
validation and is recorded as such.

### DD-9: Where simulated media attach

**Alternatives considered:**

1. **At the simulators' existing sockets** — rootcanal's HCI TCP port,
   netsim's `PacketStreamer`, `wmediumd`'s frame socket — each exposed as a
   port and joined by an ordinary forward.
2. **A dedicated bridge driver tier** — purpose-built `LinkEndpoint` drivers
   that know about radios.
3. **Inside the guest** — a shim in Android proxying Bluetooth/Wi-Fi at the
   HAL or socket layer.

**Decision:** Option 1.

**Rationale:** Every simulator involved is already something a process
connects to over a socket, and `jumpstarter-driver-bt-peer` already proves
the pattern by attaching `bumble` to rootcanal at
`tcp-client:127.0.0.1:7300`. Once forwards exist, "bridge a radio" reduces to
"forward the socket that was always there", and a `TcpNetwork` child pointed
at `127.0.0.1:7300` is configuration rather than code.

Option 2 was in an earlier draft of this JEP and is now rejected as invented
machinery: it added a driver interface, a driver tier, and a medium taxonomy
to express something the existing network drivers plus a direction already
express. Option 3 is rejected because it changes the device under test — a
guest-side shim means the Bluetooth stack being exercised is not the one that
ships, invalidating precisely the pairing and handover behavior these tests
exist to verify.

One consequence to be explicit about: not every port is a transparent splice.
netsim's `PacketStreamer` requires the attaching side to originate a
`StreamPackets` call with `ChipInfo` before traffic flows, so its consumer is
a protocol-terminating driver, not a raw socket. Forwards carry both kinds;
the difference lives in the driver at the `requires` end.

### DD-10: Wi-Fi medium forwarding

**Alternatives considered:**

1. **Forward the `mac80211_hwsim` frame socket** between members so both
   share one simulated medium.
2. **Attach at netsim's 802.11 MAC chip** — Wi-Fi as another chip kind on the
   `PacketStreamer` port.
3. **Forward the projection socket at L4** — skip radio simulation and carry
   the projection session's TCP connection directly.

**Decision:** Option 1 as the target, with option 2 taken first where
netsim's Wi-Fi support covers the configuration; option 3 rejected as a
fidelity failure but retained as a diagnostic.

**Rationale:** Wireless phone projection's defining behavior is the
*handover*: pair over Bluetooth, then move the session to a peer-to-peer
Wi-Fi link. This holds for Android Auto and CarPlay alike, and it is the
reason the medium cannot be skipped. A
test that forwards the projection socket at L4 never exercises the handover,
the Wi-Fi Direct negotiation, or the failure modes that matter — it verifies
that a TCP proxy works. So the medium has to be simulated.

Between 1 and 2, option 2 is far cheaper when it applies, because it reuses
the same forward machinery as Bluetooth. Option 1 is the general answer,
because `--vhost_user_mac80211_hwsim` is the mechanism Cuttlefish documents
for sharing a Wi-Fi medium between separately-launched instances, and
`wmediumd` is where RSSI and delivery modeling live. Option 1 is also the
hardest thing in this JEP and the most likely to need upstream work — it is
scheduled last (Phase 4) and its risk is called out explicitly.

### DD-11: Physical ↔ virtual — gateway exporter

**Alternatives considered:**

1. **Gateway exporter with a real radio**, presenting the adapter as a
   `provides` port; the virtual side attaches as if to any other controller.
2. **Emulate the physical device's peer in software** and never involve RF.
3. **Require both halves to be the same kind** — no hybrid benches.

**Decision:** Option 1.

**Rationale:** A physical device's radio is inside the device; its HCI is not
reachable from outside, so no software path puts a real phone and a simulated
head unit on the same medium without a real radio somewhere. Option 1 puts
that radio in exactly one place and keeps it a normal exporter with a normal
driver, so it schedules, leases, and reports like everything else.

Option 2 is a useful *test double* but is not a hybrid bench: it is a virtual
bench with a hand-written model of the physical device, and it cannot find
bugs in the physical device's stack. Option 3 gives up the differentiator in
the Motivation.

The consequence is stated plainly: hybrid benches require lab hardware and
physical proximity between the gateway and the device, and the gateway is a
shared RF resource that must be modelled as such.

### DD-12: Access policy and port validation timing

**Alternatives considered:**

1. **Per-member policy evaluation, bind-time port validation.** Each member
   is evaluated against `ExporterAccessPolicy` exactly as a standalone lease
   would be; forwards are validated against the bound exporters' reports.
2. **Selection-time port validation** — ports surfaced as exporter CR labels
   so member selectors only match exporters that have the required ports.
3. **A bench-shaped policy CRD** with rules over lease shape, size, and count.

**Decision:** Option 1 for v1; option 2 recorded as a follow-on that depends
on JEP-0015; option 3 deferred.

**Rationale:** Per-member evaluation is the least surprising and most secure
default: a multi-member lease can never reach an exporter its client could
not have leased directly, which makes the feature non-escalating by
construction. The lease's effective priority is the minimum across members
and its maximum duration the minimum of the per-member `maximumDuration`
values, so a bench never outlives or outranks its most restricted member.
`status.members[].priority` records each member's own value so the
aggregation is auditable.

Port validation is bind-time in v1 because of a concrete constraint: lease
selection matches `labels.Set(exporter.Labels)` — **CR metadata labels** —
while reported ports live in `ExporterStatus.Devices[]`. Ports are therefore
visible to the controller *after* binding but not selectable *before* it. The
bind-time check (both named ports exist, directions are complementary,
declared protocols agree) needs no new machinery and catches every
misconfiguration, at the cost of holding the devices while it reports
`Invalid` — which is arguably better for debugging anyway.

Option 2 is strictly better and worth doing: it would make a bench whose
ports cannot be satisfied report `Unsatisfiable` **without holding
anything**, consistent with DD-1's all-or-nothing property. It requires
reported device information to be reconciled into selectable exporter labels,
which is precisely the mechanism proposed in JEP-0015. Hand-maintained CR
labels are a stopgap but drift from reality, so this JEP does not depend on
them.

Option 3 is a real requirement but separable, and should be designed once
there is operational experience with what benches people actually build.

## Design Details

### Binding: one pass, one write

The existing `reconcileStatusExporterRef` generalizes to
`reconcileStatusMembers`, keeping its selection pipeline intact per member:

```text
for each member in spec.members:
    approved   = policy-approved exporters matching member.selector
    online     = filter offline
    unleased   = filter out exporters claimed by other active leases
                 AND exporters already picked earlier in this same pass
    ready      = filter out exporters still cleaning up a prior lease
    candidate[member] = best(ready)   # in memory only, nothing written yet

if any required member has no candidate:
    set Pending/Unsatisfiable with the failing role named
    write NO member claims
    requeue

else:
    status.members = candidates        # all of them
    status.priority = min(member priorities)
    single Status().Update()           # atomic
```

Two properties follow, and they are the reason for DD-1:

- **No partial holds.** Candidates live in memory until every required member
  resolves. A lease that cannot complete writes no claims at all, so it holds
  nothing while it waits and there is nothing to release on timeout.
- **No self-collision.** Because the pass excludes exporters already chosen
  for an earlier member, two roles can share a selector without both
  resolving to the same exporter — which is what a `phone` + `phone`
  two-handset bench needs.

The single-exporter path is the same code with one member: `spec.selector` is
normalized into a synthetic member at admission, and on success the result is
written to `status.exporterRef` rather than `status.members` (DD-2). There is
one selection implementation, not two.

**What is unchanged, and still imperfect.** Exclusivity is still a read-scan
of other leases' claims against a possibly-stale cache, so two leases can
race for one exporter and both write, because they write different objects.
This is pre-existing and neither improved nor worsened here; the loser is
detected on a later reconcile and re-bound.

### Lease state

```text
      ┌─────────┐
      │ Pending │◄────────────┐ requeue: some member unavailable
      └────┬────┘             │ (nothing held)
           │                  │
    ┌──────┴───────┐          │
    ▼              ▼          │
┌──────────────┐ ┌────────────────────┐
│Unsatisfiable │ │ all members bound   │──┘
└──────────────┘ │ (one status write)  │
                 └─────────┬──────────┘
                           │ forwards validated + requested
                           ▼
                    ┌────────────┐  forward failure  ┌──────────┐
                    │ ForwardsUp │──────────────────►│ Degraded │
                    └─────┬──────┘                   └──────────┘
                          │ all forwards connected
                          ▼
                    ┌────────────┐  member lost   ┌──────────┐
                    │   Ready    │───────────────►│ Degraded │
                    └─────┬──────┘                └──────────┘
                          │ release / expiry / spec.release
                          ▼
                    ┌────────────┐
                    │   Ended    │
                    └────────────┘
```

Conditions reuse the existing `LeaseConditionType` values — `Pending`,
`Ready`, `Unsatisfiable`, `Invalid` — with `ForwardsReady` and `Degraded`
added. `Ready` for a multi-member lease requires all required members bound
*and* all declared forwards connected. Expiry, `status.ended`, the
`jumpstarter.dev/lease-ended` label, and `spec.release` behave exactly as for
a single-exporter lease, because it is the same object and the same code.

### Forward validation and establishment

Validation happens after binding, because the report that proves a port
exists belongs to a bound exporter, not to a selector (DD-12). For each
`spec.forwards[]` entry the controller checks, against
`ExporterStatus.Devices[].Ports`:

1. `from.member` and `to.member` name declared members — enforced by CEL at
   admission, before this point.
2. Both named ports exist on the respective bound exporters.
3. `from` resolves to a `PROVIDES` port and `to` to a `REQUIRES` port.
4. If both ports declare `protocol`, the values are equal (DD-8).

A failure sets `Invalid` with the offending forward and reason named, and
leaves the members bound so the user can inspect the bench rather than
staring at a lease that silently refuses to bind.

Establishment then reuses the existing port-forward primitives:

1. The controller instructs both exporters over their existing `Listen`
   streams.
2. Each calls `DialPeer(lease, forward, member)`.
3. The controller mints two tokens with an identical `stream` claim derived
   from `(lease UID, forward name)` — a UUIDv5 in a fixed namespace, so the
   value is stable across reconciles and both ends compute the same
   rendezvous without coordination. `aud: jumpstarter router`,
   `sub: jumpstarter exporter`, expiry clamped to the lease's end time.
4. Both call `RouterService.Stream`.
5. The `provides` side dials its local service and splices with
   `forward_stream`. The `requires` side opens a `TemporaryTcpListener` on
   its declared `listen` address and splices each accepted connection.
6. **Fast path**: with a `peer_endpoint`/`peer_token`, each exporter races a
   direct dial against the router path; first handshake wins, loser is reset.
   The direct listener authenticates `peer_token`, so a direct forward is not
   a weaker trust boundary. `mode: direct` fails rather than falling back;
   `mode: router` never attempts it.

**Failure modes and handling:**

| Failure | Behavior |
| --- | --- |
| Named port absent on a bound exporter | Lease `Invalid`; members stay bound for inspection |
| `provides → provides` or `requires → requires` | Lease `Invalid` (DD-6) |
| Declared protocols disagree | Lease `Invalid` (DD-8) |
| Protocols differ but neither declared | Connects, fails at first byte — accepted (DD-8) |
| Forward references an undeclared member | Rejected by CEL at admission; lease never created |
| `listen` address already bound on the exporter | Lease `Invalid` naming the port and address |
| Router stream drops mid-lease | Re-dial with backoff; `Reconnecting`; `Degraded` after a grace period |
| Direct dial fails (fast path) | Silent fallback to the router path; recorded as a metric |
| A member's exporter disappears | Peer's stream resets; lease `Degraded` naming the role (DD-3) |
| Client releases the lease | Forwards torn down first, then the lease ends normally |

### Concurrency and ordering

Reconciliation is single-writer per lease (standard controller-runtime work
queue), so no intra-lease locking is needed, and member selection is pure
computation followed by one write. Forward splicing on the exporter side runs
in the existing per-driver task group, so a stalled forward cannot block
driver calls on other children.

Ordering between forwards and drivers is safe by construction: forwards come
up before `Ready`, and a `requires`-side driver only dials when the client
invokes it — `bt-peer` connects its transport on `start()`, well after the
lease is Ready.

Because binding is a single status write, the read-your-writes hazard a
two-object design would face does not arise.

### Security

- **No privilege escalation by construction** (DD-12): every member is
  evaluated against `ExporterAccessPolicy` exactly as a direct request would
  be, so a multi-member lease reaches only exporters its client already could.
- **Forward authorization is lease-scoped**: `DialPeer` verifies the calling
  exporter is currently bound to the named member of the named lease and that
  the named forward lists it. Tokens expire with the lease.
- **A forward is a data path between two devices under one client's control**,
  not a general exporter-to-exporter tunnel: an exporter can reach only a peer
  a lease it is bound to explicitly names, and only through the named port.
- **Ports are opt-in surface.** A driver that declares no ports cannot be
  forwarded to or from. A `requires` port is the only new inbound socket, it
  binds an address the exporter chose, and it exists only while the lease
  holds it.
- **The fast path is authenticated** — a direct peer connection presents
  `peer_token`; it is not trusted for being on the same network. Exporters
  supporting it open a listener, disabled by default and enabled per exporter
  configuration.
- **`members` is immutable after creation**, so a bound lease cannot be
  widened beyond what it was authorized for.
- **Physical RF is not access-controlled.** A gateway exporter's radio is
  audible to anything in range; labs must treat RF proximity as a trust
  boundary.

### Observability

JEP-0013 telemetry gains `lease.member` as a span attribute wherever
`lease.name` already appears, so per-role activity is separable within one
lease, plus per-forward counters (bytes each direction, reconnects, mode
actually used, direct-dial fallback rate). Time-to-bench (lease create →
`Ready`) is the headline metric; it is the existing lease-acquisition metric
extended to record the member count.

## Test Plan

### Unit Tests

- `LeaseSpec` CEL validation: extended one-of rule, members/scalar mutual
  exclusion, role-name uniqueness, forwards referencing declared members,
  member immutability.
- Member selection: all-or-nothing binding, no self-collision, correct
  `Unsatisfiable` role naming, and — the property DD-1 rests on — that a
  reconcile which cannot satisfy every member writes **no** member claims.
- Single-exporter regression: existing lease controller tests pass
  unmodified, and a one-member lease produces the same `status.exporterRef`
  as the equivalent scalar lease.
- Aggregation: priority = min(member priorities), duration clamped to
  min(member `maximumDuration`).
- Port report round-trip: driver-declared ports reach
  `ExporterStatus.Devices[].Ports`; an exporter reporting none is treated as
  non-forwardable; a report with no `ports` field is accepted unchanged.
- Forward validation: missing port, `provides→provides`,
  `requires→requires`, protocol disagreement, and `listen` collision each
  produce `Invalid` with the offending forward named.
- `Dial` without `member_name` on a multi-member lease returns
  `INVALID_ARGUMENT` listing roles; with a valid role, routes correctly.
- `DialPeer` token issuance: identical `stream` claim for both ends,
  stability across reconciles, expiry clamped to lease end, rejection when
  the caller is not bound to the named member.
- Python client: role attribute access, `connect()` returning a bare client
  for single-member and a role mapping for multi-member,
  `bench.forwards[...]` state, raising on a `Degraded` role.

### Integration Tests

Against a kind cluster with the controller and mock exporters (`e2e/`):

- Two mock exporters, one two-member lease, one forward between an
  `EchoNetwork` provides-port and a requires-port — end-to-end establishment
  through the real router with no device-specific code.
- Contention: more concurrent multi-member leases than capacity, asserting
  every lease is either fully bound or holding nothing.
- Lease expiry, explicit release, and client disconnect; assert no leaked
  router streams, no exporters left claimed, and no listeners left bound.
- Router-mode vs. direct-mode selection, including forced fallback.
- `jmp get lease -o mobly` output validated against Mobly's testbed schema.
- **Compatibility**: an N-1 client against an N controller for the full
  single-exporter workflow; an N client issuing a single-exporter lease
  against an N-1 controller; an N-1 *exporter* (reporting no ports)
  registering against an N controller.

### Hardware-in-the-Loop Tests

- **Virtual ↔ virtual**: two `jumpstarter-driver-cuttlefish` exporters in
  separate Pods (JEP-0016 `ExporterSet`), forwarded at rootcanal/netsim.
  Assert BT discovery and pairing, and — Phase 4 — Wi-Fi association.
  Runnable in CI on KVM-capable nodes with no lab hardware. This is the
  headline result and should run on every merge once it exists.
- **Physical ↔ physical**: a physical phone and head unit on two exporters
  **on different lab hosts** — the allocation ATS's `SimpleScheduler`
  refuses. Requires lab hardware; runs on a labeled runner.
- **Hybrid**: physical phone + gateway exporter (USB HCI dongle) + virtual
  head unit in-cluster.
- **Latency characterization**: HCI round-trip through a router forward vs. a
  direct forward vs. host-local rootcanal, reported as a distribution. Its
  result determines whether A2DP-class workloads are in scope for router mode.

### Manual Verification

- `jmp shell --lease` ergonomics: role-prefixed driver calls,
  `j forward status`, Ctrl+C teardown leaving no held leases.
- `jmp get exporter` port discovery: a user who has never seen an exporter's
  config can construct a working forward from its output alone.
- An existing Mobly multi-device suite run unmodified against an exported
  testbed.
- `jmp get leases` and `kubectl get leases` on a mixed set of single- and
  multi-member leases.

## Acceptance Criteria

**Lease plane**

- [ ] `spec.members[]` / `status.members[]` on `Lease`, with extended CEL
      validation and member immutability
- [ ] Binding is one `Status().Update`: a reconcile that cannot satisfy every
      required member writes no member claims (verified by test)
- [ ] Two members with identical selectors bind two distinct exporters
- [ ] Contention test: concurrent multi-member leases over insufficient
      capacity leave no lease partially holding devices
- [ ] `ExporterAccessPolicy` evaluated per member; a multi-member lease
      cannot reach an exporter its client could not lease directly
- [ ] Lease priority = min(member priorities); duration clamped to
      min(member `maximumDuration`)
- [ ] `status.exporterRef` unchanged for single-exporter leases and nil for
      multi-member ones; existing lease controller tests pass unmodified
- [ ] `Dial` without `member_name` on a multi-member lease returns
      `INVALID_ARGUMENT` naming the roles

**Ports and forwards**

- [ ] `PortReport` is optional on `DriverInstanceReport`; exporters reporting
      no ports register and operate unchanged
- [ ] Declared ports appear in `ExporterStatus.Devices[].Ports` and in
      `jmp get exporter` output
- [ ] `listen` addresses never appear in any report, CR status, or lease spec
- [ ] Forward validation rejects missing ports, `provides→provides`,
      `requires→requires`, and declared-protocol mismatch, naming the forward
- [ ] Forwards establish over `RouterService` with no `router.proto` change
- [ ] Direct fast path is authenticated, falls back automatically, and is
      observable (mode + fallback-rate metrics)
- [ ] `bt-peer` participates as a `requires` endpoint with **no Python
      changes** — exporter configuration only
- [ ] Byte fidelity and reset semantics verified by the `EchoNetwork`
      integration test

**Topologies** (each a phase gate, in order)

- [ ] **Phase 1 — Virtual ↔ virtual Bluetooth**: two CVDs in separate Pods on
      separate nodes complete BR/EDR discovery and pairing over a forwarded
      rootcanal/netsim port, in CI
- [ ] **Phase 2 — Physical ↔ physical across hosts**: a phone and head unit
      on exporters on different lab hosts complete a phone projection
      session (Android Auto in the reference implementation)
- [ ] **Phase 3 — Hybrid**: a physical phone pairs with a virtual head unit
      through a gateway exporter
- [ ] **Phase 4 — Virtual Wi-Fi**: two CVDs in separate Pods associate over a
      forwarded `mac80211_hwsim`/`wmediumd` medium, and a projection
      session completes the Bluetooth → Wi-Fi handover end to end
- [ ] Measured HCI round-trip latency through a router forward is published,
      with a documented statement of which workloads it does and does not
      support

## Graduation Criteria

### Experimental

`members`, `forwards`, and port reporting ship behind a controller feature
gate, with Phases 1–2 complete. Signals sought: do real benches stay at two
members or grow; how often does the direct fast path apply; does anyone hit
`MaxItems=8`; how often do `listen` collisions occur on physical hosts; does
the nil-`status.exporterRef` convention (DD-2) surprise any consumer; is
bind-time port validation (DD-12) painful enough to justify accelerating the
JEP-0015 dependency.

### Stable

- Phases 1–4 complete, with Phase 4 green in CI for 30 consecutive days
- At least two `requires`-side drivers outside this JEP's reference set
  (evidence the port model generalizes)
- No API changes to `members` / `forwards` / `PortReport` for one release
  cycle
- Selection-time port validation either shipped on JEP-0015 or explicitly
  deferred with a rationale

## Backward Compatibility

This proposal is **additive**, and deliberately concentrates compatibility
risk in one field, handled by DD-2.

- **CRD**: `Lease` gains two optional spec lists and two optional status
  lists; `ExporterStatus.Devices[]` gains an optional `ports` list. No
  existing field changes type, meaning, or default. `Exporter`,
  `ExporterAccessPolicy`, `ExporterSet`, and `VirtualTargetClass` are
  otherwise untouched. Every lease that exists today validates unchanged.
- **`status.exporterRef`**: unchanged for single-exporter leases — every
  lease created before this feature and every one created after that does not
  pass `members`. Multi-member leases leave it nil, which existing consumers
  already read as "not bound yet" (DD-2). The JEP-0016 façade,
  `jmp get leases`, the `Dial` path, and JEP-0013 telemetry need no changes
  to keep working correctly; they need changes only to *support* benches.
- **Driver report**: `ports` is a new optional repeated field. An exporter
  built before this JEP reports nothing and is treated as non-forwardable —
  the correct answer. No version negotiation, no migration.
- **Drivers**: unchanged. Ports are declared in exporter configuration; the
  reference `requires` endpoint (`bt-peer`) needs no Python changes at all.
  A driver with no ports is simply not a forward endpoint.
- **Protocol**: three new fields on existing messages and one new RPC.
  Unknown fields are ignored by proto3, so an N-1 client talks to an N
  controller unchanged. An N client requesting `members` from an N-1
  controller has them silently dropped — so the client probes for `DialPeer`
  (or a controller version) and fails with a clear message rather than
  acquiring a one-device lease it will misuse.
- **Operator upgrade**: a CRD schema addition, a standard bundle bump with no
  conversion webhook. Rolling back removes multi-member leases; single-exporter
  leases are unaffected, because they are ordinary leases in every respect.
- **Coexistence**: single- and multi-member leases share one exporter pool,
  one scheduler, and one selection implementation.

## Consequences

### Positive

- Multi-device testing becomes a first-class Jumpstarter concept, with atomic
  acquisition, shared lifetime, and no deadlock.
- **Acquisition atomicity is structural, not engineered.** All of a lease's
  claims are in one object written once, so there is no partial-hold state,
  no acquisition timeout, no release-and-retry, and no gang scheduler.
- **A bench can span hosts** — the specific allocation ATS's `SimpleScheduler`
  refuses, and the reason multi-device testing is capped at one lab machine
  everywhere today.
- **The data plane is existing code.** Forwards are `TemporaryTcpListener` +
  `forward_stream` with a router peer stream substituted for a client stream;
  `router.proto` is untouched and no driver changes.
- A bench is a lease, so `jmp create lease`, expiry, `spec.release`, access
  policy, telemetry, and `kubectl get leases` all apply unchanged. No second
  resource, no second RBAC surface, no second lifecycle.
- **Ports are discoverable**, so a client can construct a forward from
  `jmp get exporter` output instead of tribal knowledge or someone's YAML.
- Phone projection becomes expressible — Android Auto, CarPlay, and
  head units on Android, Linux, or QNX alike — and in the virtual-to-virtual
  form expressible *in CI without a lab*.
- Cross-host virtual device pairing, which no shipping tool does, reduces to
  forwarding a socket.
- Hybrid benches let labs virtualize the abundant half of a bench and keep the
  scarce half real.
- Nothing here is Android-specific: CAN cross-connects between two ECUs,
  serial cross-overs, and SOME/IP peer benches are all ports and forwards.
- The exporter = DUT invariant survives; JEP-0016 DD-8's option 1 becomes
  available.

### Negative

- **`LeaseSpec` now has two shapes.** Scalar and members forms are mutually
  exclusive, enforced by CEL, on the most heavily validated object in the API.
  Every future change to lease semantics must reason about both.
- **`status.exporterRef` becomes conditional** — set only for single-exporter
  leases. A documented, fail-safe convention (DD-2), but a subtlety every new
  consumer must learn.
- **Ports are a third place to configure things**, alongside driver config and
  labels, and a `requires` port hard-codes a local address that can collide.
- **Protocol mismatch can fail late** when neither port declares `protocol`
  (DD-8) — a deliberate retreat from admission-time validation.
- **Port validation happens after binding** (DD-12), so an unsatisfiable
  forward holds devices while reporting `Invalid`, until JEP-0015 makes ports
  selectable.
- **No per-member early release.** A client finished with one device cannot
  hand it back without ending the lease.
- **A new inbound socket on exporters** — the `requires` listener, plus the
  optional direct fast-path listener.
- **Latency is a first-class risk**, not a footnote, and some timing-sensitive
  protocols may not work in router mode.
- **Hybrid benches need new lab hardware** and physical proximity.
- **Phase 4 depends on upstream behavior** we do not control.

### Risks

- **Wi-Fi frame forwarding may not be viable over the router.** `wmediumd`
  assumes medium-like timing; head-of-line blocking on a TCP substrate may
  make association flaky or impossible except on the direct fast path.
  Mitigation: Phase 4 is last, may conclude "direct mode only", and the
  datagram work in Future Possibilities is the escalation path.
- **Bluetooth timing may be tighter than measured** — pairing may work while
  A2DP streaming does not. Mitigation: latency characterization is an
  acceptance criterion whose answer is published, not assumed.
- **`listen` collisions on physical hosts.** A lab host already running the
  service a `requires` port wants to shadow cannot host that endpoint.
  Mitigation: validation names the conflict explicitly; ephemeral-port
  allocation is a Future Possibility.
- **Multi-member leases amplify the pre-existing binding race** — N chances
  to lose a race, so re-bind churn grows with bench size. Mitigation:
  `MaxItems=8` bounds it; the real fix is the global scheduler the code
  already has a `TODO` for.
- **The scalar/list convention may leak** — some consumer may assume a bound
  lease always has `status.exporterRef`. Mitigation: fail-safe by design, and
  the compatibility matrix covers known consumers.
- **netsim and `vhost_user_mac80211_hwsim` are internal-ish interfaces** that
  upstream may change. Mitigation: attach at documented seams, pin the
  runtime image, treat divergence as a contribution opportunity.
- **Bench sprawl.** Mitigation: per-member policy limits reach, `MaxItems=8`
  bounds size, bench-level quota is an explicit Future Possibility.

## Rejected Alternatives

- **Not doing this.** Multi-device testing stays a client-side workaround with
  no atomicity and no device-to-device path, JEP-0016 DD-8 stays blocked, and
  Jumpstarter inherits the same one-host ceiling as every existing tool.
- **A separate `LeaseGroup` CR owning N child leases** — DD-1. An earlier
  draft proposed exactly this; it was rejected once the binding path was
  examined, because N members in one object bind in one write whereas N child
  leases make partial acquisition a durable state requiring machinery that
  the single-object design never needs.
- **Naming it `LeaseSet`.** `Set` already means ReplicaSet/HPA semantics
  here: `ExporterSet` (JEP-0014) is N *identical* instances from a template
  with `minReplicas`/`maxReplicas`. A bench is heterogeneous members addressed
  by role, so `LeaseSet` beside `ExporterSet` would actively mislead. (The
  Kubernetes name for gang-scheduled heterogeneous members is `PodGroup`,
  hence the earlier `LeaseGroup` — but DD-1 removes the need for any new kind.)
- **Promoting `status.exporterRef` to a list** — DD-2.
- **Raw addresses in the lease spec** — DD-6. Leaks exporter-private
  architecture into a client-authored manifest and breaks when a lab
  re-addresses a service.
- **Untyped endpoints without direction** — DD-6. Cannot reject the
  controller-to-controller wiring that is the most likely mistake.
- **A `medium` taxonomy** (`bluetooth | wifi | uwb | …`) — DD-8. Validates
  what traffic represents rather than what it is, and would approve a
  raw-HCI-to-netsim forward, the one pairing most needing rejection.
- **A mandatory wire-format token** — DD-8. Right axis, but still approves
  controller-to-controller, and imposes a taxonomy to define and version for
  a check that direction already makes.
- **A dedicated `LinkEndpoint` driver interface and bridge-driver tier** —
  DD-9. Invented machinery for something existing network drivers plus a
  direction already express; a `TcpNetwork` child at `127.0.0.1:7300` is
  configuration, not code.
- **Encoding ports as driver labels** — DD-7. Zero schema change, but a
  `requires` port has no backing driver instance to label, forcing phantom
  report entries and two parallel key namespaces.
- **Client-relayed forwards** — DD-4. Retained as an explicit
  `mode: client-relay` debug transport, not the architecture.
- **Direct peer-to-peer only** — DD-4. Fails on the hybrid topology that
  motivates the design.
- **A peer-specific RPC in `router.proto`** — DD-5.
- **Guest-side Bluetooth/Wi-Fi shims** — DD-9. Changes the device under test.
- **L4 forwarding of the projection session's socket** — DD-10. Skips the
  handover, which is the thing under test. Kept as a diagnostic.
- **N independently-leased devices behind one exporter.** Ruled out by
  JEP-0016's exporter = DUT invariant. A group as a single composite DUT
  (JEP-0016 DD-8 option 2) remains legitimate and orthogonal.
- **Building a Jumpstarter multi-device *test runner*.** This JEP stops at the
  bench. Tradefed, Mobly, and pytest already run multi-device tests well;
  Jumpstarter's contribution is the bench they run against.
- **Adopting ATS/OmniLab as the fleet layer.** Mobile Harness's `Device`
  abstraction is ADB-shaped and runs in the lab-server JVM beside the device,
  its OSS platform coverage is Android-only, and extending it means in-tree
  Java and Bazel. Adopting it would import the one-`LabLocator` constraint
  this JEP removes. The complementary direction — Jumpstarter *under* ATS via
  a Mobile Harness `Device` or Mobly-controller shim, so existing results
  pipelines keep working — is a Future Possibility, not a rejection.

## Prior Art

- **OmniLab Android Test Station / Mobile Harness** (`google/device-infra`)
  is the closest system and the one to be precise about. It **does**
  gang-schedule multi-device jobs: `AdhocTestbedSchedulingUtil` performs
  maximum-cardinality bipartite matching of `SubDeviceSpec`s onto idle
  devices, with `TestbedDevice`, `CompositeDevice`, and
  `SubDeviceSynchronizationDriver` all present, and it has a multi-host mode
  (one controller, N workers) for fleet pooling. The structural limit is in
  `SimpleScheduler.allocate`: every device locator in an allocation must
  share one `LabLocator`, so a multi-device job cannot span lab hosts. That
  single check is the gap this JEP targets. *Terminology note for reviewers:*
  a Mobile Harness `Driver` is a **test runner** (TradefedTest, MoblyTest),
  not a device abstraction — its `Device`/`BaseDevice` is the analog of a
  Jumpstarter driver.
- **Tradefed multi-device** contributes the role/requirement declaration
  pattern (`<device name="...">`) and `multi_target_preparer` for coordinated
  setup such as Bluetooth pairing, and documents its own gap verbatim: *"No
  APIs yet exist to conduct operations from one device to another, such as
  `device1.sync(device2)`."*
- **LAVA MultiNode** is the closest HiL prior art: one job spans multiple
  devices with named roles, and — notably — makes the multi-device unit *the
  job itself* rather than a wrapper around N jobs, which independently
  supports DD-1. Its synchronization primitives (`lava-sync`, `lava-send`,
  `lava-wait`) coordinate *test scripts*, not devices; there is no bridged
  medium between roles.
- **Mobly** contributes the testbed format this JEP exports to.
- **`kubectl port-forward`** is the mental model for a forward, including its
  guarantee — it delivers bytes to a socket and does not verify the consumer
  speaks the protocol (DD-8).
- **Cuttlefish multi-instance connectivity** (`--num_instances`, shared
  `--vhost_user_mac80211_hwsim`, netsim, rootcanal, `wmediumd_control`)
  defines what "correct" looks like for the virtual media this JEP forwards.
- **The Android emulator networking backplane (36.5)** independently
  validates the demand, and its same-host scope defines the boundary this JEP
  moves.
- **`bumble`** contributes the virtual-controller model and is already a
  Jumpstarter dependency via `jumpstarter-driver-bt-peer`, whose existing
  `tcp-client:127.0.0.1:7300` transport is the proof that a driver needs no
  changes to become a `requires` endpoint.
- **Kubernetes gang scheduling** (Volcano `PodGroup`, coscheduling, Kueue
  `Workload`) is the reference for what DD-1's option 2 would have required.
  Worth noting *why* Kubernetes needs it and this JEP does not: a Pod's
  placement is recorded on the Pod, so N Pods are N objects and gang
  scheduling must be layered on top. A Jumpstarter lease records its claims on
  itself, so N devices can be one object.

## Unresolved Questions

To resolve during review:

- **Can two CVDs be joined at rootcanal directly, or is netsim required?**
  HCI is host-to-controller, and two rootcanals are both controllers — so the
  Phase 1 topology may need netsim in the middle as the component that
  presents a controller to multiple hosts. This determines whether Phase 1's
  forward is `rootcanal → controller` or `netsim → chip`, and it is a
  prototype question, not a design one.
- **Should the scalar and members forms really be mutually exclusive?** The
  alternative is `spec.selector` as a default for members that omit their
  own — convenient for homogeneous benches, but two ways to express one thing.
- **How should `listen` collisions be handled?** A fixed address keeps drivers
  unchanged but can collide on a physical host; an ephemeral port avoids
  collisions but requires telling the driver its address, reintroducing
  coupling. Currently fixed-and-validated.
- **Should a lease-level "sync" primitive exist?** LAVA provides `lava-sync`.
  Cross-role barriers are implementable in the client library, but a
  controller-mediated barrier would work across independently-driven roles.
- **Where does the exported Mobly testbed get its ADB endpoints?** Local
  forwards from `jmp shell --lease` couple the export to a live session; a
  long-lived per-member forward managed by the client library is the
  alternative.
- **How are gateway exporters modeled?** As a member with its own role
  (schedulable and auditable, but benches become three members where users
  think in two), or as an attribute of the physical member's exporter?

To resolve during implementation:

- Exact `ListenResponse` variant shape for forward setup instructions.
- Whether the fast path should race the router dial or attempt direct first
  with a short timeout — a measurable question.
- Whether forward reconnect should preserve the medium's logical state or
  force a fresh pairing; likely protocol-specific.
- How `jmp get leases` renders N exporters in a table column readably.

## Future Possibilities

Not part of this proposal:

- **Selection-time port validation** via JEP-0015 dynamic exporter labels, so
  a bench whose ports cannot be satisfied reports `Unsatisfiable` without
  holding anything (DD-12).
- **A global lease scheduler** with a view of all leases and exporters, which
  the controller already carries a `TODO` for; it would close the binding race
  for single- and multi-member leases alike.
- **Ephemeral `listen` allocation** for `requires` ports, removing the
  collision constraint at the cost of driver coupling.
- **Fan-out forwards** — one `provides` port serving several `requires` ports,
  for shared media with more than two participants. Today's forward is
  strictly point-to-point.
- **Bench-level access policy and quota** (DD-12).
- **Per-member early release**, if the all-or-nothing lifetime proves coarse.
- **Datagram forwards.** `wmediumd` and other frame-oriented media want
  datagram semantics with real boundaries and no head-of-line blocking; the
  additive `FRAME_TYPE_DATAGRAM` extension to `RouterService.Stream` (and,
  further out, QUIC unreliable datagrams) is a separate protocol JEP, for
  which Phase 4 is the most compelling justification.
- **Jumpstarter under ATS** — a Mobile Harness `Device` or Mobly-controller
  shim backed by a Jumpstarter lease, so Google's results pipeline keeps
  working while gaining non-Android and cross-host devices. Complements
  JEP-0016's Cloud Orchestrator seam.
- **A `Bench` template CRD** — a reusable named topology instantiated by
  reference, the way `VirtualTargetClass` is to `ExporterSet`. A *template*,
  not a second lease-like object, so it does not reopen DD-1.
- **Spawned-on-lease members** — a member satisfied by provisioning a new
  JEP-0014 pool instance on demand, making a bench elastic in its virtual half.
- **Agent-facing bench skills** — an agent leasing a two-device bench to
  reproduce an interaction bug, following JEP-0016's agent-native framing.

## Implementation History

- 2026-09-01: JEP drafted

## References

- [JEP-0014: Virtual Scalable Exporters](JEP-0014-virtual-scalable-exporters.md)
  — "Composite leases — multiple exporters linked into one logical lease"
  (Future Possibilities), which this JEP realizes
- JEP-0015: Dynamic Exporter Labels — the mechanism selection-time port
  validation depends on (DD-12)
- JEP-0016: Cuttlefish Kubernetes-Native Orchestration — DD-8, whose option 1
  this JEP implements
- [JEP-0013: Metrics, Tracing, and Log Observability](JEP-0013-observability-telemetry-logs.md)
- [JEP-0011: Protobuf Introspection and Interface Generation](JEP-0011-protobuf-introspection-interface-generation.md)
  — the introspection direction port reporting extends
- [google/device-infra](https://github.com/google/device-infra) — Mobile
  Harness, the ATS 2.0 engine (`SimpleScheduler`, `AdhocTestbedSchedulingUtil`)
- [OmniLab Android Test Station user guide](https://source.android.com/docs/core/tests/development/android-test-station/ats-user-guide)
- [Virtual devices in OmniLab ATS](https://source.android.com/docs/core/tests/development/android-test-station/ats-virtual-devices)
- [Tradefed: run tests with multiple devices](https://source.android.com/devices/tech/test_infra/tradefed/architecture/advanced/multi-device)
- [Cuttlefish: test connectivity of multiple devices](https://source.android.com/docs/devices/cuttlefish/connectivity)
- [Mobly](https://github.com/google/mobly) — [testbed tutorial](https://github.com/google/mobly/blob/master/docs/tutorial.md)
- [Bumble, a Python Bluetooth stack](https://google.github.io/bumble/) —
  [transports](https://google.github.io/bumble/transports/index.html)
- [netsim (`platform/tools/netsim`)](https://android.googlesource.com/platform/tools/netsim/) —
  `proto/netsim/packet_streamer.proto`
- [google/android-cuttlefish](https://github.com/google/android-cuttlefish)
- [Test Multi-Device Interactions with the Android Emulator](https://android-developers.googleblog.com/2026/04/Test-Multi-Device-Interactions-with-the-Android-Emulator.html)
- [LAVA MultiNode](https://docs.lavasoftware.org/lava/multinode.html)

---

*This JEP is licensed under the
[Apache License, Version 2.0](https://www.apache.org/licenses/LICENSE-2.0),
consistent with the Jumpstarter project.*
