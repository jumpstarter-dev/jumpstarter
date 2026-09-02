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

### Why the exporter, not the device, is the unit of a lease

That granularity is deliberate, and this JEP keeps it. An exporter is a
bench: a DUT plus its harness, and often several devices bolted to that
harness and wired to each other. They share cabling, power and a host, so one
composite driver exposes them and one lease hands over the whole assembly.
Leasing a device *inside* such a bench would let two clients hold opposite
ends of one cable.

The gap is the opposite arrangement: a phone racked on one side of the lab
and a head unit bench on the other — two exporters, possibly on two hosts (a
host can run several), with nothing physical between them and no reason to
add it. Only the scheduler and a data path can join those. So the unit stays
the exporter and only the count changes, which is what makes the gain
combinatorial: N phones and M head unit benches give N×M benches out of N+M
exporters, any pairing gang-scheduled, none pre-wired.

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

**Phone projection is the worked example** throughout this JEP, because it
exercises the hardest version of both requirements. It is a two-device
protocol by construction: phone and head unit pair over **Bluetooth**, then
the head unit hands the session to a peer-to-peer **Wi-Fi** link, Bluetooth
having nowhere near the bandwidth for continuous video. Validating it means
holding both devices at once, driving both and asserting on both — a
phone-only lease cannot observe the handover at all. That structure holds for
every projection protocol in use and for head units running Android, Linux or
QNX; it is a property of projection, not of one vendor's stack. It also needs
two things existing tooling lacks: **heterogeneous benches**, and **pairing
two virtual devices to each other**.

Running that on Jumpstarter today means hand-rolling everything the lease
layer should provide:

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

### The virtual case: host-local by construction

For virtual devices the connectivity problem is sharper. Cuttlefish and the
Android emulator can already pair virtual devices to each other — but only
within one host. Cuttlefish shares its virtual radio media between instances
launched together, or launched separately against the *same* `wmediumd`
socket and rootcanal/netsim daemon; both are host-local Unix sockets and
loopback TCP ports. The emulator's networking backplane is explicitly scoped
to "all running instances on the same host machine", and container wrappers
publish each container's ports while keeping radio simulation inside the
container group.

The common thread in the tooling we surveyed is that virtual multi-device
testing assumes *both devices live on one machine*. That is exactly the
assumption a cluster-scheduled pool of one-device-per-Pod exporters
(JEP-0016) breaks, and why its DD-8 deferred multi-device groups pending "a
cross-Pod virtual-radio story… real upstream-facing work." If someone has
solved the cross-host case in a way this survey missed, that is worth raising
in review — it would change the build-or-adopt calculation.

The encouraging part is that these simulators are reached over ordinary
sockets: rootcanal accepts HCI on TCP — which is why
`jumpstarter-driver-bt-peer` can already attach a `bumble` peer with
`transport: "tcp-client:127.0.0.1:7300"` — netsim accepts virtual chips over
a bidirectional gRPC stream, and `wmediumd` speaks over a frame socket.
Nothing about "same host" looks fundamental to these interfaces; it appears
to be an artifact of where the sockets are reachable from, rather than a
property of the simulators themselves.

### Prior art's ceiling

Existing multi-device test frameworks stop in the same place, and the limit
is structural rather than accidental. They *can* gang-schedule several
devices into one job — role declarations, composite devices and coordinated
setup steps are all standard — but the devices must hang off a single lab
host: allocation refuses a job whose devices are attached to different hosts,
and where a multi-host mode exists it pools jobs across hosts rather than
letting one job span them. None of them offers a path *between* two devices;
the frameworks say so themselves, noting the absence of any API for
conducting an operation from one device against another. Their device
abstractions also tend to be single-platform in practice, so a bench pairing
a phone with a Linux or QNX head unit is out of reach before scheduling is
even a question.

This JEP targets exactly that seam:

- **Physical ↔ physical**: two devices on two exporters on **different lab
  hosts**, in one lease.
- **Virtual ↔ virtual**: two emulated devices in two Pods, on two nodes,
  pairing over Bluetooth and handing a session to Wi-Fi.

**Scope.** This JEP covers **homogeneous benches** — two virtual devices, or
two physical devices. Mixing them in one bench (a real phone paired to a
virtual head unit) is a natural extension and the eventual prize, but it
needs real radio hardware bridging the two worlds and is deferred to keep v1
tractable (DD-11).

The virtual case is the interesting business case: labs have scarce physical
head units and abundant phones, or the reverse, and virtualizing the abundant
half while keeping the scarce half real is a cost and throughput win no
host-local scheduler can offer.

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

`requires` is the genuinely new concept: the driver will dial a local
address, and the exporter binds an inbound forward there.

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

Note what did *not* change: `bt-peer`'s Python is untouched and its
`transport` still points at `127.0.0.1:7300`. The lease makes that address
resolve to a rootcanal in another Pod on another node — a driver that can
talk to a local service now talks to a remote one without knowing.

### Declaring a bench

```yaml
apiVersion: jumpstarter.dev/v1alpha1
kind: Lease
metadata:
  name: projection-bench
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
      between:
        - { member: headunit, port: rootcanal }
        - { member: phone,    port: controller }
```

No addresses, no port numbers, no medium taxonomy — and no statement of which
side listens. The lease names roles and port names; both sides' internals
stay inside their exporters (DD-6), and the controller resolves direction
from the reported ports at bind time (DD-13).

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
      between:
        - { member: gateway, port: can0 }
        - { member: node,    port: can }
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
    --member phone=device-type=phone \
    --member headunit=device-type=headunit \
    --forward bt=headunit.rootcanal:phone.controller \
    --duration 45m
projection-bench

$ jmp get lease projection-bench
NAME                 ENDED   CLIENT      EXPORTER                       AGE
projection-bench   false   ci-runner   phone=rack3-phone-4,            12s
                                         headunit=virt-hu-7b2c
```

Port names are discoverable rather than tribal knowledge, which is the point
of putting them in the report (DD-7):

```console
$ jmp get exporter virt-hu-7b2c -o json | jq '.status.devices[].ports'
[{"name":"rootcanal","direction":"provides","protocol":"hci-h4"}]
```

In a shell, roles become top-level names alongside the usual driver clients:

```console
$ jmp shell --lease projection-bench
jumpstarter ⚡ projection-bench ➤ j phone adb shell getprop ro.product.model
phone-under-test
jumpstarter ⚡ projection-bench ➤ j headunit power on
jumpstarter ⚡ projection-bench ➤ j forward status
NAME   FROM                  TO                  MODE     STATE       A→B       B→A
bt     headunit.rootcanal    phone.controller    direct   connected   1.2 MiB   0.9 MiB
```

In Python, the existing `lease()` context manager grows `members` and
`forwards`, and a multi-member lease yields a mapping of roles to the same
client objects a single-exporter lease yields directly:

```python
from jumpstarter.config.client import ClientConfigV1Alpha1

config = ClientConfigV1Alpha1.load("default")

with config.lease(
    members={"phone": "device-type=phone", "headunit": "device-type=headunit"},
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

        assert phone.adb.shell("dumpsys bluetooth_manager | grep -c Connected") == "1"
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
$ jmp get lease projection-bench -o mobly > testbed.yml
$ mobly_test.py -c testbed.yml --test_bed projection-bench
```

which emits a Mobly testbed whose `AndroidDevice` controllers point at the
per-member ADB endpoints Jumpstarter has forwarded locally, with the role
name carried through as the Mobly device label; other frameworks' device-set
formats map the same way. This is the integration seam: the tests and the
results pipeline do not change, while the *bench* changes from "devices
sharing one lab host" to "any mix of physical and virtual devices anywhere
the controller can reach."

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
5. **Fast path**: if `DialPeer` says the pair is direct-eligible — both
   members in one network zone, which two exporter Pods in a cluster are —
   the `requires` side dials the peer directly first and uses the router
   stream only if that does not complete quickly (DD-4). Two virtual targets
   in a cluster therefore talk over the Pod network, and the same lease keeps
   working unchanged when one member is an edge device the other cannot
   reach.

```text
       ┌──────── Lease: projection-bench (one object) ─────────┐
       │  status.members:                                        │
       │    phone    → Exporter/rack3-phone-4                     │
       │    headunit → Exporter/virt-hu-7b2c                     │
       └───────┬─────────────────────────────────┬───────────────┘
               │                                 │
  ┌────────────▼────────────┐       ┌────────────▼────────────┐
  │ Exporter: rack3-phone-4  │       │ Exporter: virt-hu-7b2c  │
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

First, the **data plane** — ports that a forward carries:

| Stack | Port | Direction | Notes |
| --- | --- | --- | --- |
| Shared virtual controller (Bumble) | `controller` | provides | Accepts multiple hosts and mediates between them (DD-9) |
| `jumpstarter-driver-bt-peer` | `controller` | requires | A Bumble `Device` dialing an external controller |
| rootcanal HCI (Cuttlefish, emulator) | `rootcanal` | provides | HCI on TCP (`7300 + rootcanal_instance_num`); hosts attach to it |
| rootcanal link layer | `rootcanal-link` | provides | Controller-to-controller federation (`7400`, `7600` BLE); standalone rootcanal only, not netsim |
| `wmediumd` / `mac80211_hwsim` | `hwsim` | provides | vhost-user, not a byte stream — reached through a frame bridge (DD-10) |
| Projection server on a phone (developer mode) | `projection`, `projection-wifi` | provides | Same service, reached over ADB (USB-like) or the guest's Wi-Fi address (wireless-like); the receiver dials it |
| Projection receiver (desktop or head unit) | `phone` | requires | Dials the phone's port (DD-10) |
| Wireless projection receiver on a head unit | `projection-rx` | provides | A TCP port on the head unit; the phone dials it after the Bluetooth handover — physical head units only |
| `socketcand` / CAN-over-TCP bridge | `can` | provides | A CAN segment reachable as a socket |
| Serial bridge (pty or TCP) | `console` | requires/provides | Cross-over between a DUT and a companion |

Second, the **control plane** — drivers that configure and observe a medium
without carrying its traffic. `jumpstarter-driver-netsim` is the worked
example: it speaks netsim's REST API to list devices, toggle radios, patch
state, reset, and start/stop/download **pcap captures** of the simulated air.
It is not a forward endpoint and needs none of this JEP's machinery; the two
compose, with the netsim driver observing the medium a forward connects.

Two consequences of the table generalize beyond radios. HCI is
**asymmetric** — a host attaches to a controller, so forwarding one device's
HCI port into another's achieves nothing, which is why forwards are
directional and `provides → provides` is rejected (DD-6); joining two
controllers is a different port, which is why the table lists both (DD-9).
And not every port is a transparent splice: netsim's `PacketStreamer`
requires the attaching side to originate a call carrying `ChipInfo` before
traffic flows, so its consumer is a protocol-terminating driver rather than a
raw socket. Forwards carry both kinds; the difference lives in the driver at
the `requires` end.

The two bench kinds are asymmetric too. For **two virtual devices** the
medium is simulated, so a forward carries it. For **two physical devices**
the medium is the air: devices in RF range pair with no forward at all, and
forwards carry only that bench's wired media — a CAN segment, a serial
cross-over. What a physical bench needs is the lease plane, which is what
lets its two exporters live on different hosts.

### A projection bench, concretely

**Virtual.** The phone is a Cuttlefish exporter booting a vendor phone image
— the stack that ships on retail devices, not an AOSP approximation — with
the projection app installed and its developer-mode server started by the
driver. The head unit is a *receiver* run as a process inside the head-unit
exporter by a `projection-rx` driver, because an automotive Android CVD
cannot receive a session: the receiver is part of the vendor stack, not of
AOSP. The receiver is a TCP client, so it is the `requires` side and the
phone `provides` the port it dials:

```yaml
spec:
  members:
    - name: phone
      selector: { matchLabels: { device-type: phone, projection: "true" } }
    - name: headunit
      selector: { matchLabels: { device-type: projection-receiver } }
  forwards:
    - name: session
      between:
        - { member: phone,    port: projection-wifi }
        - { member: headunit, port: phone }
```

`projection-wifi` reaches the phone's server through the guest's Wi-Fi
interface — the shape of a wireless session — where `projection` reaches it
over ADB, the shape of a USB session. The forward is the same either way; the
port name is the test's statement of which cable it is pretending to be
(DD-13).

**Physical.** A head unit on one exporter and a phone on another, on
different hosts. The radios are the air, so the handover needs no forward;
what this bench needs is the lease plane, and its forwards carry only wired
media — a CAN segment feeding the head unit, a serial console. A software
receiver can stand in for the head unit here too, giving a physical phone a
hardware-free counterpart.

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
    Name string `json:"name"`

    // Symmetric form (preferred): exactly two endpoints, in any order. The
    // controller resolves which is `provides` and which is `requires` from
    // the reported ports at bind time (DD-13).
    // +kubebuilder:validation:MinItems=2
    // +kubebuilder:validation:MaxItems=2
    Between []ForwardEndpoint `json:"between,omitempty"`

    // Explicit form: use when the wiring should be pinned regardless of what
    // the exporters report. Mutually exclusive with Between.
    From *ForwardEndpoint `json:"from,omitempty"`  // must resolve to `provides`
    To   *ForwardEndpoint `json:"to,omitempty"`    // must resolve to `requires`

    // Auto (default) | Router | Direct | ClientRelay.
    // Auto prefers a direct peer connection when both members are in the
    // same network zone and falls back to the router; Direct fails rather
    // than falling back; Router never attempts a direct dial (DD-4).
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
validate forwards against bound exporters. `ExporterStatus` itself gains two
optional fields used only to decide direct eligibility:

```go
    // Address peers in the same zone can dial for a direct forward, if this
    // exporter runs a peer listener. Never a device port (see Security).
    PeerEndpoint string `json:"peerEndpoint,omitempty"`
    // Opaque reachability domain. Two exporters are candidates for a direct
    // forward only if both report the same value.
    NetworkZone  string `json:"networkZone,omitempty"`
```

The existing CEL rules are extended, not replaced — the current
"one of selector or exporterRef is required" rule gains a `members` arm, plus
new rules for mutual exclusion, unique role names, forwards referencing
declared members, member immutability (mirroring `tags` and `context`), and
exactly one of `between` or `from`+`to` per forward.

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
  optional string peer_endpoint = 3;    // set when the pair is direct-eligible
  optional string peer_token = 4;       // authenticates a direct dial
  bool prefer_direct = 5;               // Auto resolved to direct-first
}
```

`ReleaseLeaseRequest`, `ListLeasesRequest`, `RouterService`, and
`router.proto` are untouched.

**CLI surface** — existing commands, new flags:

- `jmp create lease --member role=selector --forward name=m.port,m.port`
  (endpoint order is irrelevant — direction is resolved at bind time; where a
  lab uses the same port name on both sides, `--forward bt` expands to it)
- `jmp get lease[s]` prints per-role exporters; `-o json|yaml|name` unchanged
- `jmp get lease <name> -o mobly`
- `jmp get exporter <name>` shows declared ports
- `jmp shell --lease <name>`, `j <role> <driver> ...`, `j forward status`
- `jmp delete lease` / `jmp update lease` need no changes

### Hardware Considerations

- **No new lab hardware is required.** Homogeneous benches use what a lab
  already has: two physical devices pair over the air as they always have,
  and two virtual devices need no radio at all. The radio bridging hardware
  that a mixed physical/virtual bench would need is out of scope (DD-11).
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
  that budget is an acceptance criterion, and it is why an in-cluster bench
  takes the direct path by default (DD-4) and keeps the router as the
  fallback that works everywhere.
- **Wi-Fi frame forwarding is the most latency-sensitive path.** `wmediumd`
  models RSSI-based delivery and expects medium-like timing; a TCP substrate
  introduces head-of-line blocking a real air interface does not have, and
  its vhost-user transport needs a frame bridge on each side before any
  forward is involved (DD-10).
- **No head unit hardware is needed to project.** Google's Desktop Head
  Unit is the receiver for virtual benches and can stand in for one on a
  physical phone's bench (see *A projection bench, concretely*).
- **Listener addresses.** A `requires` port binds a fixed local address,
  which is safe because each exporter owns its network namespace (see
  *Deployment assumptions*). The one configuration that breaks this is a
  host-networked container sharing a machine with other exporters, which is
  outside the supported deployment model.
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
for: an edge-device exporter behind NAT and a cluster Pod, which are
not mutually routable. Jumpstarter's value in that topology is that the
controller and router are the only things both sides must reach.

Option 1 keeps one authentication model — a forward is authorized by the
lease that names it, exactly as a stream is authorized by the lease that
names it — while permitting the fast data plane where it is available. A
bench that works over the router works everywhere, and `mode: router` makes
the slow path explicit for tests that need reproducible timing.

**Direct is preferred, not merely permitted, when both members sit in the
same network zone.** For two virtual targets in one cluster — the case this
JEP is mostly for, and the case JEP-0016 produces by construction, since its
provisioner renders both benches as Pods under one `ExporterSet` — `Auto`
resolves to a direct Pod-to-Pod connection and falls back to the router only
if that dial does not complete. Three reasons, in order of weight:

1. **The router is a shared component and bench traffic is sustained.** A
   projection session or an A2DP stream is not a burst; N benches funnelling
   media through one router deployment makes a central bottleneck out of
   something the CNI would carry for free, and in a cloud install it can
   also mean paying to leave and re-enter the network.
2. **The router path in a typical install leaves the cluster and comes
   back.** It therefore inherits the ingress's failure modes — the reload
   that cut a live bench's HCI splice during verification was an ingress
   worker drain, not a Jumpstarter fault. A direct forward between two Pods
   stays inside the CNI and never meets that machinery.
3. **The workloads that are latency-sensitive are the ones still ahead.**
   Measured on the bench, the router costs **0.65 ms** on a round-trip
   (0.70 ms through the forward against 0.05 ms direct to the same
   endpoint), while an HCI command takes **~45 ms either way** because
   rootcanal answers on its own schedule. Simulated Bluetooth pairing
   therefore cannot tell the two apart — which is exactly why preferring
   direct is free — but the Phase 4 frame bridge, where `wmediumd` expects
   medium-like timing, and sustained projection throughput are where a 14×
   round-trip difference starts to decide whether a bench works at all.

That last measurement is what makes the preference safe rather than a
gamble: falling back to the router costs sub-millisecond on the path that
has been measured, so a bench that cannot get a direct connection is slower
in a way nothing so far can detect. Eligibility is a property of the pair,
not a user decision — see *Direct eligibility* under Design Details.

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

1. **Share one rootcanal** — the second CVD is launched with
   `--rootcanal_instance_num` pointing at the first, and a forward supplies
   that instance's HCI port.
2. **Federate two rootcanals at the link layer** — each CVD keeps its own
   controller, and a forward joins them at rootcanal's `link_port`.
3. **A shared virtual controller from Bumble** — its virtual `Controller`
   plus `RemoteLink` relay, replacing the simulator entirely.
4. **A dedicated bridge driver tier** — purpose-built `LinkEndpoint` drivers
   that know about radios.
5. **Inside the guest** — a shim in Android proxying Bluetooth/Wi-Fi at the
   HAL or socket layer.

**Decision:** Options 1–3 are all ordinary forwards and all supported.
Option 2 is the Phase 1 default, option 1 the fallback, option 3 the general
answer beyond Cuttlefish.

**Rationale:** Cuttlefish already solves multi-device Bluetooth on one host,
and how it does so says what to forward: all four rootcanal ports derive from
`rootcanal_instance_num` rather than the CVD's own instance number
(`hci 7300+N`, `link 7400+N`, `test 7500+N`, `link_ble 7600+N`), and sharing
one controller between devices is a documented, first-class configuration
that the guest reaches through a TCP connector. Everything host-local about
it is a **port**, which is what this JEP forwards.

Option 2 is preferred for symmetry: each device keeps its own controller, so
neither exporter's failure removes the other's radio, and rootcanal exposes
`link_port`/`link_ble_port` specifically for joining controllers to each
other. It needs no new code — a `TcpNetwork` child and a forward. Option 1 is
the fallback if federation misbehaves; equally free, but asymmetric, since one
Pod becomes the medium and therefore a single point of failure. One
constraint separates them: `link_port` exists only on standalone rootcanal,
never on netsim, so a device routing its radios through netsim has options 1
and 3 but not 2.

Option 3 is the general answer and the only one that works outside
Cuttlefish. Bumble's virtual `Controller` attaches to a link-layer bus that
several controllers can share, and its `RemoteLink` carries that bus over a
relay — the N-way medium DD-6 deferred. Choose it for a non-Cuttlefish
device, for more than two participants, or when the medium itself must be
scripted or fault-injected, which a Python controller does and a C++
simulator does not. Bumble is already a dependency:
`jumpstarter-driver-bt-peer` passes its `transport` string straight to
`bumble.transport.open_transport`, so any transport moniker works with no
Python change — that is what makes the `requires` side free. The driver only
ever builds a `Device`, a host, so the controller side is new code, and small.

Options 4 and 5 are rejected. Option 4 invents machinery — a driver
interface, a driver tier and a medium taxonomy — to express what network
drivers plus a direction already express. Option 5 changes the device under
test: a guest-side shim means the stack being exercised is not the one that
ships, invalidating the pairing and handover behavior these tests exist to
verify.

Option 2 is an instance of a general pattern rather than a Bluetooth special
case. What a bench needs is a **counterparty**: something presenting itself
to the DUT as whatever it expects on the other side of the medium. For
Bluetooth that is a shared controller; for a vehicle bus it is a *restbus*,
simulating the remaining ECUs. Both are ordinary `provides` ports, so the
same machinery serves them (integrating a real restbus is future work).

One caveat is carried rather than hidden: a Python medium is comfortable for
advertising, pairing and control but an open question for sustained
A2DP-class traffic, which compounds the latency risk already recorded.
Options 1 and 2 keep the medium in native C++ and avoid it.

**Verified on real devices (2026-09-01 and 2026-09-02).** Options 1 and 2
were exercised by hand on a single-node kind cluster with two Cuttlefish
exporter Pods — an automotive head unit in one, a phone in the other — first
with a 50-line TCP relay standing in for the forward endpoint, then over
Jumpstarter's own router path with the merged `jumpstarter-driver-bt-peer`
playing the phone. Driven through `adb` alone, every variant produced the
same result: inquiry, SSP numeric comparison showing one passkey on both
screens, a bond, and HFP, A2DP and AVRCP `Connected`, with music streaming
continuously over the link and the phone reconnecting by itself after a
Bluetooth off/on. Boot-to-bond is about two minutes, most of it guest boot.
Which profiles appear at all is decided by the phone *image*, not by the
medium: a plain GSI enables only the LE-audio profile properties, and the
classic set had to be added to the image before anything beyond the bond
connected. None of this changes the decision — option 2 stays the Phase 1
default on its symmetry — but it turns "nothing to build" into a specific
list of duties.

*What the ports actually require.* Under option 2 both CVDs run standalone
rootcanal (`--netsim_bt=false`), which binds all four ports on `0.0.0.0`;
the join itself is a runtime `add_remote` command on the *test* channel, so
federation needs two forwarded link ports **plus a control step after the
forward is up** — a post-establish hook, not something a static `forwards[]`
entry expresses. It also joins the LE phys wholesale, so the peer's beacons
appear in local scans. Under option 1 the second CVD starts no controller and
its guest dials the shared HCI port at boot, so the forward must exist
*before* that CVD launches — a pre-launch ordering constraint where option 2
has a post-launch one — and because netsim binds its HCI port on loopback
only, the forward endpoint must live inside the owning Pod rather than
merely nearby, which is what this design already provides.

*The peer side is free, and can be the whole phone.* `bt-peer` ran unchanged
as the `requires` end, its `transport` pointed at a forwarded HCI port: the
head unit discovered it, SSP-paired it, and opened AVDTP to its A2DP source
— the claim under *Ports* that the driver's Python is untouched, demonstrated
on merged code. Adding an HFP Audio Gateway alongside that source (a
`bumble.rfcomm` server plus `hfp.AgProtocol` and its SDP record) made the
head unit show both Phone and Media, and a simulated inbound call from the
peer arrived in the head unit's telephony stack as a ringing call. The
cheapest useful bench is therefore one virtual device and a Python process,
and `bt-peer` should grow a `profiles:` config rather than a sibling driver.
This exercises Bumble as a *host*; option 3, Bumble as the *controller*, is
still unverified.

*What the router costs.* Measured from one Pod against the same endpoint: a
round-trip through a router-carried forward was **0.70 ms** median (p90
0.91 ms, n=100) against **0.05 ms** direct Pod-to-Pod, while an HCI command
to rootcanal took **~45 ms either way** — the simulator's own scheduling
dominates by two orders of magnitude. Pairing cannot tell the transports
apart, which is what makes DD-4's preference for a direct in-cluster
connection free rather than a gamble. Where that stops being true is what the
*Latency characterization* test is for.

*Duties this puts on the drivers and the forward endpoint.*

- **Keep the test channel private, and watch the controller.** A client that
  connects to rootcanal's test port and closes before its banner is written
  aborts the process (`Check failed: written == size, errno = 32`).
  `process_restarter` brings rootcanal back, but the guest's connector took
  `SIGPIPE`, is not restarted, and its Bluetooth stays half-up until a `cvd
  restart`. So the test channel is never a `provides` port, a forward
  endpoint never probes liveness by connect-and-close — state comes from the
  splice — and a driver whose controller dies reports the forward `Failed`
  and the lease `Degraded` instead of leaving a bench that cannot pair.
- **Assign addresses per member, before the host powers on.** rootcanal names
  the *n*-th live device on a model `da:4c:10:de:00:<n>` and reuses numbers,
  so two federated CVDs start life with the same BD_ADDR and any attaching
  peer collides with an existing one. The rootcanal driver sets an address
  per member in its post-establish hook, ahead of power-on, because secure
  pairing binds the address into key derivation.
- **Expect identical guest networks.** Every Cuttlefish guest boots the same
  address plan behind an identical AP, so anything bridging guests at L2 or
  L3 — the Phase 4 frame bridge, a Wi-Fi Direct emulation — must NAT or
  re-address. An L4 forward never sees it.
- **Reconnect, and do it in the endpoint.** The verification cluster's
  ingress reloads on any unrelated `Ingress` change and drains its workers
  240 s later, which cut the exporter's controller stream and the forwarded
  HCI splice within seconds of each other; the exporter re-dialed its own
  stream, the forward did not, and the bench stayed `Ready` with the
  simulated phone off the air. A driver that opens its transport once at
  start cannot notice this, so the `requires` side re-dials and re-splices
  transparently and the reconnect reaches drivers that care as an event.
- **One process per exporter identity.** Two processes registered under one
  identity split connections between them; the one that does not own the
  lease's session fails `RouterService.Stream` with a `KeyError` and closes,
  which presents as a flaky forward rather than a misconfiguration.
- **Match versions across a splice.** A forward endpoint built against a
  newer network driver than the exporter's runtime accepted connections and
  returned EOF at the first byte, silently — an argument for the endpoint
  being the exporter's own code, as specified.

Two notes for whoever writes the tests: a Bumble peer keeps its bonds in
memory unless a keystore is configured, so restarting it invalidates the
DUT's link key and the DUT must forget the bond first; and deleting a `Lease`
object out from under a waiting client leaves that client retrying
`not found` forever — leases are released, not deleted (*Lease state*).

### DD-10: Wi-Fi and projection — what a forward carries

**Alternatives considered:**

1. **Forward the `mac80211_hwsim` frame socket** between members so both
   share one simulated medium.
2. **Attach at netsim's 802.11 MAC chip** — Wi-Fi as another chip kind on the
   `PacketStreamer` port.
3. **Forward the projection session at L4** — carry the projection's own TCP
   connection, over the guest's real Wi-Fi NIC, and simulate no radio.

**Decision:** Option 3 is the standard projection path and a Phase 2
deliverable: the session is an ordinary `provides`/`requires` pair and needs
nothing this JEP does not already build. Options 1 and 2 remain the target
for **medium** fidelity — association, RSSI, and the Bluetooth→Wi-Fi
handover — with option 2 first where netsim covers it, and option 1 now
understood to need a frame-bridge component on each exporter rather than
the byte forward. That is Phase 4.

**Rationale:** An earlier draft rejected option 3 as a fidelity failure —
"it verifies that a TCP proxy works" — and kept it only as a diagnostic.
Running it changed that judgment. With the two Pods of the DD-9 experiment, a
vendor phone image and a publicly available receiver projected a live session
across a single forwarded port (the verification below): version negotiation,
the TLS authentication between the phone stack and the receiver, service
discovery, the phone-side first-run flow, and the rendered launcher with
video, audio and input all ran unchanged. That is not a proxy check; it is
the whole projection stack running on two exporters.

What option 3 does not exercise is precisely bounded: the **handover** — the
credential exchange over Bluetooth and the phone joining the head unit's
Wi-Fi Direct group — and radio-layer failure modes such as RSSI, roaming and
channel loss. Those are what Phase 4 is for. Everything downstream of the
handover runs over the forward, so a lab gets a real projection workload on
day one, in CI, on hardware-free nodes, and Phase 4 arrives as a fidelity
upgrade rather than as the first time anything projects.

Two facts from the same experiment shape how the medium options are built:

- **The Cuttlefish Wi-Fi medium is not a byte stream.** The guest's
  `virtio_mac80211_hwsim` feeds a per-environment `wmediumd` over a
  **vhost-user** Unix socket (shared memory plus fd passing), with an OpenWrt
  VM as the access point. `--vhost_user_mac80211_hwsim` shares one medium
  between instances on one host only. Across hosts, option 1 is therefore a
  *bridge* — a component on each exporter terminating vhost-user locally and
  exchanging 802.11 frames with its peer — and the forward is only the pipe
  between the two bridges. Hence option 1 is both the general answer and the
  one needing new code and datagram semantics (Future Possibilities).
- **The guest's Wi-Fi is already IP-reachable.** Each guest joins its own
  OpenWrt AP and reaches the exporter's network through the AP's WAN link;
  one host route and one forwarding rule make the guest's Wi-Fi address
  reachable in return. So a Cuttlefish driver can expose a guest-side port
  two ways — over ADB, which models the USB cable, or at the guest's `wlan0`
  address, which models the Wi-Fi link. Both were run with the same result.
  They are two `provides` ports, not two mechanisms, and which one a test
  forwards is a topology choice DD-13 leaves to the test.

Between 1 and 2, option 2 is far cheaper when it applies, because it reuses
the same forward machinery as Bluetooth and netsim already owns the frames.
Option 1 is the general answer, because `wmediumd` is where RSSI and
delivery modeling live. Option 1 is also the hardest thing in this JEP and
the most likely to need upstream work — it is scheduled last and its risk
is called out explicitly.

**Verified against real devices (2026-09-01).** Same two Pods and stand-in
relays as DD-9. The phone was a Cuttlefish exporter running a vendor phone
image — a `user` build, so enabling ADB meant preparing the image rather than
configuring the guest — with the projection app sideloaded and its
developer-mode server started on the port the phone `provides`. The head unit
was a desktop receiver process in the other Pod, a plain TCP client and hence
the `requires` side; direction fell out of DD-13 as expected. The forward ran
first over ADB and then over the guest's Wi-Fi address through its AP.

The session negotiated its protocol version, completed a TLS handshake (so
the path must be byte-transparent), ran service discovery, walked the
phone-side first-run flow, and rendered the full launcher with maps, media
and telephony live. Video is phone→head-unit and cost about 0.6 MB for three
minutes of a mostly static screen, so an L4 forward is not the bandwidth
constraint. Three operational findings are folded into *Reference drivers for
projection*: the receiver quits on stdin EOF and needs a display; the phone's
projection server does not survive an aborted session and must be restarted;
and the phone would not join Wi-Fi while it held a validated Ethernet
network.

### DD-11: Mixed physical/virtual benches — deferred

**Alternatives considered:**

1. **Defer** — v1 supports homogeneous benches only: two virtual devices or
   two physical devices.
2. **In scope now**, via a gateway exporter owning a real radio adapter,
   presented as a `provides` port that the virtual side attaches to as it
   would to any other controller.

**Decision:** Option 1 — defer.

**Rationale:** Nothing about the lease plane or the forward mechanism
distinguishes a mixed bench; the obstacle is entirely physical. A real
device's radio is inside the device, and its HCI is not reachable from
outside, so no software path puts a real phone and a simulated head unit on
the same medium. The bridge has to happen in RF, which means new lab hardware
(a USB HCI dongle suffices for Bluetooth; Wi-Fi needs a 5 GHz radio),
physically near the device, shared between whoever is using it. Requiring
that to accept this JEP couples a software design to a hardware procurement,
and homogeneous benches already deliver both headline results — cross-host
physical benches and cross-Pod virtual pairing.

The path when it returns is option 2, and the analysis is recorded here so it
does not have to be redone. A gateway exporter stays a normal Jumpstarter
exporter with a normal driver, so it schedules, leases, and reports like
everything else, and its adapter is an ordinary `provides` port — no new
mechanism is needed, only the hardware and a decision about how to model a
shared RF resource (see Future Possibilities).

Worth naming a tempting non-answer: emulating the physical device's peer in
software and never involving RF. That is a useful *test double*, but it is
not a mixed bench — it is a virtual bench with a hand-written model of the
physical device, and it cannot find bugs in the physical device's stack.

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

### DD-13: Infer direction, never infer topology

**Alternatives considered:**

1. **Infer direction only.** A forward names its two endpoints in any order
   (`between`); the controller decides which is `provides` and which is
   `requires` from the reported ports. Which ports are joined stays explicit.
2. **Infer topology too** — auto-forward every `provides`/`requires` pair the
   bound exporters happen to expose, with no `forwards` stanza at all.
3. **Infer nothing** — the author states `from` and `to` on every forward.

**Decision:** Option 1, with option 3 retained as an explicit form.

**Rationale:** The two kinds of inference look similar and are opposites.

Direction is a **property of the drivers**. Whether rootcanal is the listening
end is a fact about rootcanal, already in the exporter's report and already
validated. Making the author restate it asks them to know exporter internals
— what DD-6 removed addresses to avoid — and the knowledge goes stale: swap a
`provides` implementation for one that dials, and every manifest naming it as
`from` is silently wrong. Inferring direction is a correctness improvement
that costs nothing, since the controller already checks the reported roles;
option 1 uses that check to *assign* rather than only to *reject*.

Topology is a **property of the test**. The same phone exporter is a
USB-attached phone when a test forwards `projection` and a wireless one when
it forwards `projection-wifi`; two exporters are a bench in one test and
independent devices in another. Option 2 makes that a property of whichever
drivers happen to be configured on whichever exporters the selectors bound,
which fails in three ways:

- **Nondeterminism across bindings.** A selector-based member binds to
  different exporters on different runs. If those exporters declare different
  ports, the bench wires itself differently run to run — the worst property a
  reproducible test can have.
- **Ambiguity is not rare.** Two members each exposing several ports turns
  matching into a bipartite matching problem. Tractable, but a silently wrong
  wiring is worse than an error.
- **It weakens a security property.** This JEP states that a forward is not a
  general exporter-to-exporter tunnel, because an exporter can reach only a
  peer *a lease it is bound to explicitly names*. Option 2 relaxes "explicitly
  names" to "happened to match", and an unintended pairing of two `console`
  ports is a data path nobody requested.

Option 3 stays available as `from`/`to` for wiring that should be pinned
regardless of what the exporters report; the controller then checks the stated
roles against the reported ones instead of assigning them.

The ergonomic complaint behind option 2 — `member.port:member.port` is
verbose — is better answered in the client: the CLI expands shorthand into an
explicit `spec.forwards[]` entry before submission, so the stored object stays
auditable. Where a lab uses one port name on both sides, `--forward bt` can
expand to it — a convention worth offering, not worth depending on.

## Design Details

### Deployment assumptions

Several properties below depend on one deployment invariant, stated here so a
reviewer deploying differently finds out from the document rather than from a
port conflict:

> **Each exporter owns its own network namespace.** In practice this is
> either a container running exactly one exporter, or a single-exporter edge
> device.

Both supported shapes satisfy it. For virtual targets, JEP-0016 supplies it
*by construction*: its `cuttlefish.jumpstarter.dev` provisioner renders one
CVD per Pod under a JEP-0014 `ExporterSet`, and a Pod is a network namespace,
so every provisioned exporter gets a private loopback without anyone having
to arrange it. For physical targets an edge device is a single exporter and
the question does not arise.

Three consequences follow, and they are why this JEP can be as small as it is:

- **`requires` ports can bind fixed local addresses.** A driver that dials
  `127.0.0.1:7300` is dialing *its own* loopback, so two exporters both using
  7300 never collide. This is what makes the zero-driver-changes property
  (DD-6) structural rather than coincidental — under a shared namespace it
  would be luck that breaks on the second exporter.
- **Control-plane blast radius equals lease scope.** A simulator control API
  scoped to "this host" is scoped to one exporter, hence to one lease. This
  matters concretely: the netsim driver's `reset` is documented as affecting
  every device on its netsim instance, which would cross lease boundaries if
  two exporters shared one.
- **DD-4's two transports map onto the two shapes.** Exporter Pods in one
  cluster are mutually routable, so the direct path applies and is preferred;
  edge devices behind NAT are exactly why the router path is the universal
  fallback and why pure peer-to-peer was rejected. The split is not a
  user-visible choice: a virtual bench gets the fast path because of where it
  runs, and the same lease spec keeps working when one member moves to a
  bench on someone's desk.

A second invariant is narrower, and cost a verification session before it
was noticed:

> **Each exporter identity is served by exactly one process.**

Two processes under one identity both register and both look healthy, but
only one owns the lease's session; connections routed to the other die on a
`KeyError` for a driver UUID it has never heard of, with no useful error on
either side. It presents as a flaky forward, not a misconfiguration. A member
exporter runs one replica, and a duplicate registration is worth refusing.

The exception to watch is host networking. The `jumpstarter-driver-cuttlefish`
container recipe currently documents `--network=host` (so that netsim and
rootcanal are reachable from outside the container), which places every
exporter on that machine in one namespace and forces port-offset schemes such
as `7681 + instance_num`. That arrangement is fine for a hand-managed
single-exporter host or local development, and is **not** a supported basis
for multiple exporters on one machine under this JEP.

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

The single-exporter path is the same code with one member: `spec.selector`
normalizes into a synthetic member at admission and the result is written to
`status.exporterRef` instead of `status.members` (DD-2). One selection
implementation, not two.

**What is unchanged, and still imperfect.** Exclusivity is still a read-scan
of other leases' claims against a possibly-stale cache, so two leases can
race for one exporter and both write. Pre-existing, neither improved nor
worsened here; the loser is detected on a later reconcile and re-bound.

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

1. Every endpoint names a declared member — enforced by CEL at admission,
   before this point.
2. Both named ports exist on the respective bound exporters.
3. **Direction resolves.** For a `between` forward, exactly one endpoint must
   report `PROVIDES` and the other `REQUIRES`; the controller assigns the
   roles accordingly (DD-13). For an explicit `from`/`to` forward, the stated
   roles must match what the exporters report.
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
6. **Fast path**: with `prefer_direct` set, the `requires` side dials
   `peer_endpoint` first and gives it a short bounded timeout (a couple of
   round-trips, not seconds), falling back to the already-minted router
   stream if it does not complete; the router dial is not raced, so an
   eligible pair does not pay for two connections on every establishment.
   The direct listener authenticates `peer_token`, so a direct forward is not
   a weaker trust boundary. `mode: direct` fails rather than falling back;
   `mode: router` never attempts it. Which transport a forward ended up on is
   in `LeaseForwardStatus.Mode`, and a fallback records why.

**Direct eligibility.** The controller offers `prefer_direct` when both bound
members report the same non-empty `NetworkZone` and the `provides` side
reports a `PeerEndpoint` (DD-4). Zone is deliberately opaque and comes from
deployment configuration — one value per cluster network, which JEP-0016's
provisioner sets on every Pod it renders — so an edge exporter reporting none
is never a direct candidate and keeps the router path with no per-lease
setup. Reachability stays a statement someone made about the deployment
rather than something the controller infers from addresses it cannot test,
the same reasoning DD-13 applies to topology. A pair that claims a zone but
cannot connect is not a user-visible failure: the dial times out and the
router stream, already minted, carries the forward.

**Reconnection belongs to the endpoint.** A forward outlives any single
stream: an ingress reload, a router restart or a network blip cuts the peer
stream, and in a cluster whose proxy reloads on unrelated `Ingress` changes
that is routine rather than exceptional (DD-9). The `requires` side therefore
re-dials with backoff and re-splices without tearing down its listener, and
the first attempt right after a cut is expected to fail and be retried.
Surfacing the reset to the driver instead does not work: these drivers open
their transport once at start, so after a silent cut the bench still reports
`Ready` while the device is off the air. Drivers that must know still learn
of it — a reconnect is an event on the forward (*Observability*), which is
how DD-10's projection server gets its mandatory restart. Forward state comes
from the splice, never from probing the far end.

**Failure modes and handling:**

| Failure | Behavior |
| --- | --- |
| Named port absent on a bound exporter | Lease `Invalid`; members stay bound for inspection |
| Both endpoints `provides`, or both `requires` | Lease `Invalid`; direction cannot resolve (DD-6, DD-13) |
| Explicit `from`/`to` contradicts the reported directions | Lease `Invalid` naming the forward and the reported roles |
| Declared protocols disagree | Lease `Invalid` (DD-8) |
| Protocols differ but neither declared | Connects, fails at first byte — accepted (DD-8) |
| Forward references an undeclared member | Rejected by CEL at admission; lease never created |
| `listen` address already bound on the exporter | Lease `Invalid` naming the port and address |
| Router stream drops mid-lease | Re-dial with backoff; `Reconnecting`; `Degraded` after a grace period |
| Ingress/proxy reload cuts the peer stream | `requires` side re-dials and re-splices transparently; the first attempt after the cut is expected to fail. Observed in verification, and invisible to drivers that dial once |
| Direct dial fails or times out | Fall back to the already-minted router stream; recorded as a metric, and conspicuous for a same-zone pair that should have connected |
| A member's exporter disappears | Peer's stream resets; lease `Degraded` naming the role (DD-3) |
| Client releases the lease | Forwards torn down first, then the lease ends normally |

### Reference drivers for projection

Nothing in the projection bench needs new forward machinery, but the
verification (DD-10) showed that the two reference drivers own real work,
of the same kind as DD-9's rootcanal control hook:

- **`cuttlefish` (phone).** A retail phone image is a `user` build, so the
  image the driver boots must be prepared rather than configured: ADB enabled
  and the exporter's key installed, and the classic Bluetooth profile
  properties present, since a plain GSI ships only the LE-audio set (DD-9).
  Both are per-image steps JEP-0016's provisioner can run once, not per-lease
  actions; recovery is `cvd restart`, which keeps userdata where `cvd rm`
  does not. For `projection-wifi` the driver brings the guest onto the
  instance's AP, routes to it, and cuts the guest's Ethernet first, because
  Android will not join Wi-Fi while it holds a validated Ethernet default.
  The projection server is started on request and **restarted whenever the
  forward resets**, because a projection session does not survive an abort —
  so a reconnect must reach the driver as an event, not be hidden in the
  splice.
- **`projection-rx` (head unit).** Runs a receiver as a process: a display,
  a dummy audio device, and its console held open, since receivers typically
  exit on stdin EOF and use that console as their stimulus API (key presses,
  day/night, microphone). Screenshots come from the display. The receiver
  dials the instant it starts, so the driver starts it only on a client call,
  after the forward is Ready — the same ordering rule `bt-peer` follows.
- **`bt-peer` (phone).** DD-9's verification showed the driver is one
  config away from being a phone rather than an audio source: with an HFP
  Audio Gateway alongside its A2DP source it satisfies both of a head unit's
  phone-facing profiles, and can ring it. That belongs in the driver as a
  `profiles:` list, with the bond keystore persisted for the life of the
  lease so a peer restart does not strand the DUT's link key.

The `cuttlefish` and `projection-rx` drivers are the reference
`provides`/`requires` pair for projection. What they must not do is know
about each other: the phone driver exposes
ports and the head unit driver dials `127.0.0.1:5277`, and the lease is the
only place the two are joined.

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
  configuration. Preferring it in-cluster (DD-4) does not widen what is
  exposed: the peer listener is one authenticated port per exporter, distinct
  from any device port, so a cluster can keep a `NetworkPolicy` that blocks
  Pod-to-Pod access to simulator ports — which are *not* access-controlled —
  while allowing the peer port between exporters. Shipping that policy with
  the Pod is JEP-0016's job, and preferring direct makes it load-bearing
  rather than advisory.
- **`members` is immutable after creation**, so a bound lease cannot be
  widened beyond what it was authorized for.
- **Physical RF is not access-controlled.** Two physical devices pairing in
  a shared lab space are audible to anything in range; labs must treat RF
  proximity as a trust boundary.
- **Simulator control ports are not access-controlled either.** Standalone
  rootcanal binds its HCI, link and test-channel ports on `0.0.0.0` and
  accepts any client; the test channel can re-address devices, join
  models, and — by accident — crash the controller (DD-9). A
  driver exposes only the HCI and link ports as `provides`, never the test
  channel, and the exporter's network policy is what limits who can reach
  them; JEP-0016 should ship that policy with the Pod.

### Observability

JEP-0013 telemetry gains `lease.member` as a span attribute wherever
`lease.name` already appears, so per-role activity is separable within one
lease, plus per-forward counters (bytes each direction, reconnects, mode
actually used, direct-dial fallback rate). Because `Auto` now prefers direct
for same-zone pairs (DD-4), the fallback rate is a health signal rather than
a curiosity: an in-cluster bench silently running over the router means the
peer listener, the zone configuration or a `NetworkPolicy` is wrong, and it
should be visible as such. Reconnects are also an *event* on
the forward, not only a counter: an endpoint driver that must re-establish
protocol state after a cut (DD-10's head unit server) subscribes to it, and
a bench whose forward is re-dialing is visibly degraded rather than quietly
deaf. Time-to-bench (lease create → `Ready`) is the headline metric; it is
the existing lease-acquisition metric extended to record the member count.

For simulated media there is a stronger signal available than byte counters.
`jumpstarter-driver-netsim` exposes netsim's pcap capture (start, stop,
download) over its REST control API, so a bench can capture the *over-the-air*
traffic of a pairing and attach it to the run — the interaction itself, not
just the fact that bytes crossed a forward. Because the control API is scoped
to one exporter's namespace, that capture is scoped to the lease. This is the
clearest illustration of the control-plane / data-plane split described under
*Attaching media*: the netsim driver controls and observes the medium, while
a forward carries it.

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
- Router-mode vs. direct-mode selection: `Auto` picks direct for two
  same-zone exporters, falls back to the router when the peer dial is
  blocked, records the mode actually used, and never falls back under
  `mode: direct`.
- `jmp get lease -o mobly` output validated against Mobly's testbed schema.
- **Compatibility**: an N-1 client against an N controller for the full
  single-exporter workflow; an N client issuing a single-exporter lease
  against an N-1 controller; an N-1 *exporter* (reporting no ports)
  registering against an N controller.

### Hardware-in-the-Loop Tests

- **Virtual ↔ virtual**: two `jumpstarter-driver-cuttlefish` exporters in
  separate Pods (JEP-0016 `ExporterSet`), joined by a forwarded rootcanal
  port (DD-9). Assert BT discovery and pairing, and — Phase 4 — Wi-Fi
  association, with `jumpstarter-driver-netsim` supplying a pcap of what
  actually crossed the air. Runs in CI on KVM-capable nodes with no lab
  hardware: the headline result, on every merge once it exists.
- **Virtual ↔ peer**: a `cuttlefish` exporter and a `bt-peer` exporter joined
  by `bt=headunit.rootcanal:phone.controller`. Assert the CVD discovers and
  pairs the peer and the driver reports `avdtp_connected`, and — with the
  peer's HFP AG enabled — that a ringing call from it reaches the head unit's
  telephony stack, covering the phone-facing profiles without a phone. The
  smallest bench, no second guest to boot; DD-9's verification ran it by hand
  over the router. Cheapest CI tier, every merge.
- **Virtual projection**: a phone CVD and a `projection-rx` exporter in
  separate Pods joined over `projection-wifi` (DD-10). Assert the receiver
  reaches the launcher and a screenshot matches. Same CI tier; the two
  compose into one bench once Phases 1 and 2 are green.
- **Physical ↔ physical**: a physical phone and head unit on two exporters
  **on different lab hosts** — the allocation the surveyed frameworks do not
  make. Requires lab hardware; runs on a labeled runner.
- **Latency characterization**: HCI round-trip through a router forward, a
  direct forward and host-local rootcanal, reported as a distribution, which
  decides whether A2DP-class workloads are in scope for router mode. By hand
  the simulator dominated on a single node (DD-9); the test looks for where
  that inverts — cross-node, sustained A2DP, physical controllers.
- **Forward resilience**: cut a live bench's peer stream (restart the router,
  or reload the ingress in front of it) and assert the forward re-establishes,
  the reconnect is counted and emitted, and a driver that dialed once at start
  is still talking to the far end.

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
- [ ] `Auto` resolves to a direct peer connection for two same-zone
      in-cluster exporters, and the fallback to the router is a measurable
      event rather than the silent normal case
- [ ] `bt-peer` participates as a `requires` endpoint with **no Python
      changes** — exporter configuration only, relying on its existing
      `open_transport(self.transport)` passthrough
- [ ] Phase 1 is achieved with **no new driver code** — a `TcpNetwork` child
      on the rootcanal port plus a forward
- [ ] A Bumble-based shared controller exists as a driver exposing a
      `provides` port, for benches outside Cuttlefish and for N-way media
- [ ] A forward survives a cut peer stream: the endpoint re-dials and
      re-splices with no driver involvement, the reconnect is counted and
      emitted as an event, and a driver that dialed once at start keeps
      working
- [ ] A second exporter process registering under a live identity is
      detected and refused rather than silently splitting connections
- [ ] Byte fidelity and reset semantics verified by the `EchoNetwork`
      integration test

**Topologies** (each a phase gate, in order)

- [ ] **Phase 1 — Virtual ↔ virtual Bluetooth**: two CVDs in separate Pods
      on separate nodes complete BR/EDR discovery and pairing over a
      forwarded rootcanal port (link-layer federation, or a shared HCI
      instance), in CI. *Demonstrated by hand for both variants on
      2026-09-01 with two Pods on one node and a stand-in relay (DD-9);
      on 2026-09-02 the router path itself carried a bench — a CVD's
      rootcanal to a `bt-peer` Pod through `RouterService.Stream`, pairing
      and profiles included. CVD ↔ CVD over the router and the multi-node
      case remain.*
- [ ] **Phase 2 — Virtual projection**: a phone CVD and a `projection-rx`
      head unit in separate Pods complete a projection session to the
      launcher over one forwarded port, in CI, with no lab hardware.
      *Demonstrated by hand on 2026-09-01 over both `projection` and
      `projection-wifi` with a stand-in relay (DD-10); the drivers, the router
      path and CI remain.*
- [ ] **Phase 3 — Physical ↔ physical across hosts**: a phone and head unit
      on exporters on different lab hosts complete a phone projection
      session
- [ ] **Phase 4 — Virtual Wi-Fi medium**: two CVDs in separate Pods
      associate over a bridged `mac80211_hwsim`/`wmediumd` medium or a
      shared netsim 802.11 chip, and a projection session completes the
      Bluetooth → Wi-Fi handover end to end
- [ ] Measured HCI round-trip latency through a router forward is published,
      with a documented statement of which workloads it does and does not
      support. *First data point, 2026-09-02: 0.65 ms of router against a
      ~45 ms rootcanal command round-trip, single node (DD-9);
      cross-node and A2DP-class throughput remain.*

## Graduation Criteria

### Experimental

`members`, `forwards`, and port reporting ship behind a controller feature
gate, with Phases 1–3 complete. Signals sought: do real benches stay at two
members or grow; how often does the direct path actually apply, and how
often does a same-zone pair fall back; does anyone hit
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
- **`status.exporterRef`**: unchanged for every lease that does not pass
  `members`. Multi-member leases leave it nil, which existing consumers
  already read as "not bound yet" (DD-2), so the JEP-0016 façade,
  `jmp get leases`, `Dial` and JEP-0013 telemetry keep working; they change
  only to *support* benches.
- **Driver report and drivers**: `ports` is a new optional repeated field, so
  an exporter built before this JEP reports none and is treated as
  non-forwardable — the correct answer, with no version negotiation or
  migration. Ports are declared in exporter configuration, and the reference
  `requires` endpoint (`bt-peer`) needs no Python changes at all.
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

- Multi-device testing becomes a first-class concept, with atomic
  acquisition, shared lifetime and no deadlock — and the atomicity is
  structural, not engineered: all of a lease's claims are one object written
  once, so there is no partial-hold state, no acquisition timeout, no
  release-and-retry and no gang scheduler.
- **A bench can span hosts** — the allocation the frameworks surveyed here
  decline to make, and the usual reason multi-device testing stays on one lab
  machine.
- **The data plane is existing code**: `TemporaryTcpListener` +
  `forward_stream` with a router peer stream substituted for a client stream.
  `router.proto` is untouched and no driver changes.
- A bench is a lease, so `jmp create lease`, expiry, `spec.release`, access
  policy, telemetry and `kubectl get leases` apply unchanged — no second
  resource, RBAC surface or lifecycle. Ports are discoverable, so a forward
  can be built from `jmp get exporter` output rather than tribal knowledge.
- Phone projection and Bluetooth pairing become expressible for any protocol
  and any head unit OS, and in virtual-to-virtual form expressible *in CI
  without a lab* — cross-host virtual device pairing reduces to forwarding a
  socket.
- Nothing here is Android-specific: CAN cross-connects, serial cross-overs
  and SOME/IP peer benches are all ports and forwards. The exporter = DUT
  invariant survives, and JEP-0016 DD-8's option 1 becomes available.

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
- **Phase 4 depends on upstream behavior** we do not control.

### Risks

- **Wi-Fi frame forwarding may not be viable over the router.** `wmediumd`
  assumes medium-like timing; head-of-line blocking on a TCP substrate may
  make association flaky or impossible except on the direct fast path. Its
  transport is also vhost-user (shared memory), so a cross-host medium needs
  a frame bridge first, not just a forward (DD-10).
  Mitigation: Phase 4 is last, may conclude "direct mode only", and the
  datagram work in Future Possibilities is the escalation path; Phase 2
  already delivers a real projection workload without it.
- **The projection bench depends on vendor artifacts this project cannot
  ship** — the phone image, the projection app and the receiver, one of which
  is not distributed outside an app store. A lab must supply them, and Phase
  2 CI needs an artifact path that does not redistribute them. Mitigation:
  the drivers take artifact locations as configuration, and the Bluetooth
  phase carries the CI headline on freely available images.
- **Bluetooth timing may be tighter than measured** — pairing may work while
  A2DP streaming does not. Mitigation: latency characterization is an
  acceptance criterion whose answer is published, not assumed.
- **The simulators are less robust than a lab needs.** rootcanal aborts when
  a test-channel client disconnects early, its restart does not reattach the
  guest, and a crash on one exporter strands every member's controller
  (DD-9). Mitigation: the driver keeps the test channel private, never
  probes, watches its controller and reports `Degraded`, and owns the restart
  as a recovery action; the abort is an upstream fix worth contributing.
- **The cluster data path is less stable than a bench assumes.** Long-lived
  streams do not survive ordinary cluster events: an nginx ingress reloads
  on any `Ingress` change and drains its workers on a timer, cutting every
  gRPC stream through it — measured on the verification cluster as a reload
  at 04:00:13 and the exporter's controller stream cut at 04:04:13, with
  the forwarded HCI splice going down 40 s later (DD-9). A
  multi-hour bench will meet this repeatedly. Mitigation: reconnection is the
  forward endpoint's responsibility, reconnects are observable, the HIL suite
  includes a bench that survives a deliberate stream cut, and an in-cluster
  bench avoids the machinery altogether (DD-4). A related hazard is silent
  version skew across a splice — an argument for the endpoint being the
  exporter's own code, as specified.
- **The namespace invariant may be violated in the field.** Fixed `listen`
  addresses and lease-scoped control-plane blast radius both assume one
  exporter per network namespace. A host-networked deployment silently
  breaks both — colliding ports, and simulator resets that reach another
  lease's devices. Mitigation: bind failures are reported as `Invalid`
  naming the port and address, and the assumption is stated explicitly;
  JEP-0016 removes the question entirely for provisioned virtual targets.
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
- **Direct peer-to-peer only** — DD-4. Fails whenever an edge-device
  exporter and a cluster Pod are not mutually routable.
- **A peer-specific RPC in `router.proto`** — DD-5.
- **Guest-side Bluetooth/Wi-Fi shims** — DD-9. Changes the device under test.
- **L4 projection forwarding *as the whole answer*** — DD-10. It is the
  Phase 2 deliverable and runs the full projection stack, but it skips the
  Bluetooth → Wi-Fi handover, which is why medium simulation stays on the
  roadmap as Phase 4.
- **N independently-leased devices behind one exporter.** Ruled out by
  JEP-0016's exporter = DUT invariant. A group as a single composite DUT
  (JEP-0016 DD-8 option 2) remains legitimate and orthogonal.
- **Building a Jumpstarter multi-device *test runner*.** This JEP stops at the
  bench. Existing runners already run multi-device tests well; Jumpstarter's
  contribution is the bench they run against.
- **Adopting an existing mobile test framework as the fleet layer.** Their
  device abstractions are Android-shaped and run beside the device on the lab
  host, and adopting one would import the single-host allocation constraint
  this JEP removes. The complementary direction — Jumpstarter *under* such a
  framework via a device shim, so existing results pipelines keep working — is
  a Future Possibility, not a rejection.

## Prior Art

- **Android/mobile multi-device test frameworks** contribute the role
  declaration pattern (a job naming `phone`, `headunit`) and coordinated
  setup steps such as pairing two devices before a test. They gang-schedule
  device sets, but allocation is confined to one lab host and none of them
  bridges a medium between roles — the gap this JEP targets.
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

- **Bumble as a Cuttlefish controller.** DD-9's options 1 and 2 are verified;
  option 3 is not. `mode=controller` is documented for the Android emulator
  but not for Cuttlefish, and it is the only option that reaches beyond
  Cuttlefish. One prototype run settles it.
- **Who issues the link-layer join, and who owns addresses?** Option 2 needs
  a control step after the forward is up, and rootcanal's address numbering
  collides for any attaching host (DD-9). Candidates: a post-establish hook
  on the `requires`-side driver, a lease-level `forwards[].onEstablish`
  action, or leaving it to the test. The first keeps the controller ignorant
  of Bluetooth, which DD-8 and DD-13 argue for.
- **Forward-before-launch ordering.** Option 1's guest dials at boot, so the
  forward must exist before the second device is created; option 2's join
  must happen after. JEP-0016's provisioner creates the device at lease time,
  so the forward has to come up between binding and creation — as a
  lease-status condition the provisioner waits on, or a retrying connector.
- **Should the scalar and members forms really be mutually exclusive?** The
  alternative is `spec.selector` as a default for members that omit their
  own — convenient when several members share a selector, but two ways to
  express one thing.
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

To resolve during implementation:

- Exact `ListenResponse` variant shape for forward setup instructions.
- What the direct-dial timeout should be before falling back to the router.
  Direct-first rather than racing is now the decision (DD-4); the number is
  measurable and the stakes are bounded, since falling back costs ~0.65 ms on
  the path measured so far.
- How an exporter's network zone is established: taken from deployment
  configuration (the assumption in *Direct eligibility*), derived by the
  controller from where the registration arrived, or probed. Configuration is
  the least clever and the only one that works for an edge exporter that is
  routable from the cluster but not the reverse.
- Whether forward reconnect should preserve the medium's logical state or
  force a fresh pairing; likely protocol-specific. Two data points: a
  projection server must be restarted after a dropped session, so the
  reconnect must be visible to endpoint drivers; and a Bumble peer loses its
  in-memory bonds when its transport is cut, stranding the DUT's link key
  unless the driver persists a keystore. For Bluetooth the answer may simply
  be "persist and re-attach", making reconnect invisible.
- How Phase 2's CI obtains the phone image, projection app and receiver
  without redistributing them (see Risks).
- How `jmp get leases` renders N exporters in a table column readably.

## Future Possibilities

Not part of this proposal:

- **Mixed physical/virtual benches** (DD-11) — a real device paired to a
  virtual one through a gateway exporter owning a real radio adapter. No new
  software mechanism: the adapter is an ordinary `provides` port. What it
  needs is lab hardware and a decision on how to model a shared RF resource —
  as its own member, schedulable and auditable but making a two-device bench
  three, or as an attribute of the physical member's exporter.
- **Selection-time port validation** via JEP-0015 dynamic labels, so a bench
  whose ports cannot be satisfied reports `Unsatisfiable` without holding
  anything (DD-12).
- **A global lease scheduler**, which the controller already carries a `TODO`
  for; it closes the binding race for single- and multi-member leases alike.
- **Fan-out forwards** — one `provides` port serving several `requires`
  ports, for media with more than two participants. Bumble's link relay
  already implements the idea as virtual *rooms*, so this is an integration
  rather than an invention.
- **Ephemeral `listen` allocation**, needed only if the
  one-exporter-per-namespace assumption is relaxed.
- **Bench-level access policy and quota** (DD-12), and **per-member early
  release** if the all-or-nothing lifetime proves coarse.
- **Datagram forwards.** Frame-oriented media want datagram semantics with
  real boundaries and no head-of-line blocking; an additive datagram frame
  type on `RouterService.Stream` (and, further out, QUIC datagrams) is a
  separate protocol JEP that Phase 4's frame bridge would consume.
- **Vehicle-bus counterparty integration.** A broker exposing CAN, LIN,
  FlexRay and Automotive Ethernet over a socket is already a `provides` port
  needing nothing new here, and composes with the automotive drivers
  Jumpstarter ships (`can`, `doip`, `someip`, `uds`, `xcp`, `obd`) as the
  counterparty they talk to. It suggests a three-member bench — a virtual
  head unit driven by real vehicle signals, a restbus supplying the rest of
  the vehicle, and a phone — but the mature options are commercial products,
  so integrating one is an out-of-scope decision deserving its own JEP.
- **Jumpstarter under an existing test framework** — a device or
  Mobly-controller shim backed by a lease, so an established results pipeline
  keeps working while gaining non-Android and cross-host devices.
- **A `Bench` template CRD** — a reusable named topology instantiated by
  reference, the way `VirtualTargetClass` is to `ExporterSet`. A *template*,
  not a second lease-like object, so it does not reopen DD-1.
- **Spawned-on-lease members** — a member satisfied by provisioning a
  JEP-0014 pool instance on demand, making a bench elastic in its virtual
  half.
- **Agent-facing bench skills** — an agent leasing a two-device bench to
  reproduce an interaction bug, following JEP-0016's agent-native framing.

## Implementation History

- 2026-09-01: JEP drafted.
- 2026-09-01: DD-9 options 1 and 2 verified by hand with two CVD Pods on a
  kind cluster — pairing, HFP/A2DP/AVRCP, A2DP streaming.
- 2026-09-01: DD-10 option 3 verified by hand — a phone CVD projected a live
  session to a receiver in another Pod over one forwarded port, then again
  with the phone end on its Wi-Fi NIC. The decision was revised on that
  evidence: L4 projection promoted from diagnostic to the Phase 2
  deliverable, the Wi-Fi medium reframed as a frame bridge, and the
  projection bench, its ports, the `projection-rx` driver and the phase
  renumbering added.
- 2026-09-01: DD-9 second pass — full classic profile stack between a
  retail-image phone CVD and the head unit over federated rootcanals, and
  `jumpstarter-driver-bt-peer` run unchanged as a `requires` endpoint.
  rootcanal's test-channel crash, address reuse and the identical guest
  address plan became driver duties, a Security bullet and a Risk.
- 2026-09-02: DD-9 third pass — the same bench over Jumpstarter's own data
  path, with a Bumble HFP-AG + A2DP peer standing in for the phone. Router
  overhead measured at 0.65 ms against ~45 ms of rootcanal; proxy-reload
  stream loss, duplicate registration and version skew became
  forward-endpoint duties, then normative text: a measured price on DD-4's
  fast path, one process per exporter identity, endpoint-owned reconnection
  with reconnects as events, a cluster-data-path Risk, a *Forward resilience*
  test and two acceptance criteria.
- 2026-09-02: `Auto` changed to **prefer a direct peer connection between
  same-zone members** rather than defaulting to the router —
  `PeerEndpoint`/`NetworkZone`, `prefer_direct`, direct-first-with-fallback
  and a *Direct eligibility* rule.
- 2026-09-02: Motivation gained *Why the exporter, not the device, is the
  unit of a lease*; vendor-specific naming replaced with generic phone
  projection and Bluetooth pairing throughout, and the verification
  narratives consolidated.

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
- [Cuttlefish: test connectivity of multiple devices](https://source.android.com/docs/devices/cuttlefish/connectivity)
- [Mobly](https://github.com/google/mobly) — [testbed tutorial](https://github.com/google/mobly/blob/master/docs/tutorial.md)
- [Bumble, a Python Bluetooth stack](https://google.github.io/bumble/) —
  [transports](https://google.github.io/bumble/transports/index.html),
  [Android / `android-netsim` `mode=controller`](https://google.github.io/bumble/platforms/android.html),
  [apps and tools](https://google.github.io/bumble/apps_and_tools/index.html)
- [jumpstarter-dev/jumpstarter#986](https://github.com/jumpstarter-dev/jumpstarter/pull/986)
  — `jumpstarter-driver-bt-peer` (merged); the reference `requires`-side
  endpoint
- [jumpstarter-dev/jumpstarter#980](https://github.com/jumpstarter-dev/jumpstarter/pull/980)
  — `jumpstarter-driver-netsim` (open); the control-plane companion, incl.
  pcap capture
- [netsim (`platform/tools/netsim`)](https://android.googlesource.com/platform/tools/netsim/) —
  `proto/netsim/packet_streamer.proto`
- [google/android-cuttlefish](https://github.com/google/android-cuttlefish)
- [Test Multi-Device Interactions with the Android Emulator](https://android-developers.googleblog.com/2026/04/Test-Multi-Device-Interactions-with-the-Android-Emulator.html)
- [LAVA MultiNode](https://docs.lavasoftware.org/lava/multinode.html)

---

*This JEP is licensed under the
[Apache License, Version 2.0](https://www.apache.org/licenses/LICENSE-2.0),
consistent with the Jumpstarter project.*
