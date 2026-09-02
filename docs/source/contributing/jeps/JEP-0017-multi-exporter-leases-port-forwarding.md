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
**Scope.** This JEP covers **homogeneous benches** — two virtual devices, or
two physical devices. Mixing them in one bench (a real phone paired to a
virtual head unit) is a natural extension and the eventual prize, but it
requires real radio hardware bridging the two worlds and is deferred to keep
v1 tractable (DD-11).

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
bt     headunit.rootcanal    phone.controller    direct   connected   1.2 MiB   0.9 MiB
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
5. **Fast path**: if `DialPeer` says the pair is direct-eligible — both
   members in one network zone, which two exporter Pods in a cluster are —
   the `requires` side dials the peer directly first and uses the router
   stream only if that does not complete quickly (DD-4). Two virtual targets
   in a cluster therefore talk over the Pod network, and the same lease keeps
   working unchanged when one member is an edge device the other cannot
   reach.

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

First, the **data plane** — ports that a forward carries:

| Stack | Port | Direction | Notes |
| --- | --- | --- | --- |
| Shared virtual controller (Bumble) | `controller` | provides | Accepts multiple hosts and mediates between them (DD-9) |
| `jumpstarter-driver-bt-peer` | `controller` | requires | A Bumble `Device` dialing an external controller |
| rootcanal HCI (Cuttlefish, emulator) | `rootcanal` | provides | HCI on TCP (`7300 + rootcanal_instance_num`); hosts attach to it |
| rootcanal link layer | `rootcanal-link` | provides | Controller-to-controller federation (`7400`, `7600` BLE); standalone rootcanal only, not netsim |
| `wmediumd` / `mac80211_hwsim` | `hwsim` | provides | vhost-user, not a byte stream — reached through a frame bridge (DD-10) |
| Android Auto head unit server (phone, developer mode) | `aa-hu`, `aa-hu-wifi` | provides | Same service, via `adb forward` (USB-like) or the guest's Wi-Fi address (wireless-like); the head unit dials it |
| Android Auto receiver (Desktop Head Unit) | `phone` | requires | Google's public receiver; dials the phone's port (DD-10) |
| Android Auto wireless receiver (GAS head unit) | `aa-wireless` | provides | TCP 5288 on the head unit; the phone dials it after the Bluetooth handover — physical head units only |
| `socketcand` / CAN-over-TCP bridge | `can` | provides | A CAN segment reachable as a socket |
| Serial bridge (pty or TCP) | `console` | requires/provides | Cross-over between a DUT and a companion |

Second, the **control plane** — drivers that configure and observe a medium
without carrying its traffic. `jumpstarter-driver-netsim` is the worked
example: it speaks netsim's REST API (`7681 + netsim_instance_num`) to list
devices, toggle radios, patch state, reset, and start/stop/download **pcap
captures** of the simulated air. It is not a forward endpoint and needs none
of this JEP's machinery; the two compose, with the netsim driver observing
the medium a forward connects.

Two consequences of the data-plane table are worth stating plainly, and both
generalize beyond radios. First, HCI is **asymmetric**: a host attaches to a
controller, so forwarding one device's *HCI* port into another's achieves
nothing — which is precisely why forwards are directional and why a
`provides → provides` forward is rejected (DD-6). Note this is a statement
about HCI specifically, not about controllers: rootcanal exposes a separate
**link-layer** port for joining controllers to each other, which is why the
table lists both (DD-9). Second, not every
port is a transparent splice — netsim's `PacketStreamer` requires the
attaching side to originate a `StreamPackets` call carrying `ChipInfo` before
traffic flows, so its consumer is a protocol-terminating driver rather than a
raw socket. Forwards carry both kinds; the difference lives in the driver at
the `requires` end.

Note the asymmetry between the two supported bench kinds. For **two virtual
devices** the medium is simulated, so a forward carries it and a shared
controller mediates it. For **two physical devices** the radio medium is the
air: real devices in RF range pair without any forward at all, and forwards
carry only the *wired* media of such a bench — a CAN segment, a serial
cross-over. The lease plane is what physical benches need most, because it is
what lets their two exporters live on different hosts.

### A projection bench, concretely

The Android Auto bench in the reference implementation has one shape that
was verified end to end (DD-10) and one that follows from it.

**Virtual.** The phone is a Cuttlefish exporter booting Google's GMS GSI —
the phone stack Google ships, not an AOSP approximation — with Android Auto
installed and its developer-mode head unit server started by the driver. The
head unit is *not* an AAOS CVD, which cannot receive projection because the
receiver is part of GAS; it is Google's Desktop Head Unit, run by a `dhu`
driver as a process inside the head-unit exporter. The DHU is a TCP client,
so it is the `requires` side, and the phone `provides` the port it dials:

```yaml
spec:
  members:
    - name: phone
      selector: { matchLabels: { device-type: android-phone, gms: "true" } }
    - name: headunit
      selector: { matchLabels: { device-type: aa-headunit } }
  forwards:
    - name: projection
      between:
        - { member: phone,    port: aa-hu-wifi }
        - { member: headunit, port: phone }
```

`aa-hu-wifi` reaches the head unit server through the guest's Wi-Fi
interface — the phone's side of a wireless session — where `aa-hu` would
reach it through `adb forward`, the shape of a USB session. The forward is
the same either way; the port name is the test's statement of which cable it
is pretending to be (DD-13).

**Physical.** A GAS head unit on one exporter and a phone on another, on
different hosts. The radios are the air, so the handover needs no forward;
the lease plane is what this bench needs, and its forwards carry only wired
media — a CAN segment feeding the head unit's VHAL, a serial console. A
`dhu` member can stand in for the head unit here too, which gives a
physical phone a hardware-free receiver.

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
   that cut a live bench's HCI splice in DD-9's third pass was an ingress
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
and reading how tells us what to forward. In
`assemble_cvd`, all four rootcanal ports are derived from
`rootcanal_instance_num` rather than from the CVD's own instance number —
`hci 7300+N`, `link 7400+N`, `test 7500+N`, `link_ble 7600+N` — and
`--rootcanal_instance_num` is documented as *"use an existing rootcanal
instance which is launched from cuttlefish instance with
rootcanal_instance_num."* Sharing a controller between devices is therefore a
first-class, supported configuration, and the guest reaches it through a TCP
connector on `rootcanal_hci_port`. Everything host-local about it is a
**port**, which is exactly what this JEP forwards.

Option 2 is preferred because it is symmetric. Each device keeps its own
controller, so neither exporter's failure removes the other's radio, and
rootcanal exposes `link_port` / `link_ble_port` specifically for joining
controllers to one another — distinct from the HCI port that hosts use. It
needs no new code at all: a `TcpNetwork` child on 7400 and a forward.

Option 1 is the fallback if link-layer federation does not behave as the flag
names imply. It is equally free of new code, but asymmetric — one exporter's
rootcanal becomes the medium for both, so that Pod becomes a single point of
failure for the bench.

Option 3 is the general answer, and the only one that works outside
Cuttlefish. Bumble's virtual `Controller` attaches to a link-layer bus,
several controllers on one bus exchange broadcast advertising and unicast ACL
data, and `RemoteLink` carries that bus over a WebSocket relay hosting
virtual *rooms* — the N-way medium DD-6 deferred. Its `android-netsim`
transport has a `mode=controller` documented as replacing netsim outright.
Choose it when a bench includes a non-Cuttlefish device, when more than two
participants share a medium, or when the medium itself needs to be scripted
or instrumented — a Python controller can inject faults a C++ simulator will
not.

One constraint separates them in practice: `link_port` is passed only to
standalone rootcanal, never to netsim, which takes `--hci_port` alone. A
device configured to route its radios through netsim therefore has options 1
and 3 available but not option 2.

Bumble is also already a dependency: `jumpstarter-driver-bt-peer` is built on
it and passes its `transport` string directly to `bumble.transport.
open_transport`, then uses the result as `controller_source`/`controller_sink`
for a Bumble `Device`. Any Bumble moniker therefore already works with no
Python change, which is what makes the `requires` side of this design free.
The gap is that the driver only ever constructs a `Device` — a host — so the
controller side is new code, and it is small.

Option 4 was in an earlier draft of this JEP and is rejected as invented
machinery: it added a driver interface, a driver tier, and a medium taxonomy
to express something existing network drivers plus a direction already
express. Option 5 is rejected because it changes the device under test — a
guest-side shim means the Bluetooth stack being exercised is not the one that
ships, invalidating precisely the pairing and handover behavior these tests
exist to verify.

Worth noting that option 2 is an instance of a general pattern rather than a
Bluetooth special case. What a bench needs is a **counterparty component**:
something that presents itself to the DUT as whatever the DUT expects on the
other side of the medium. For Bluetooth that is a controller shared by two
hosts; for a vehicle bus it is a *restbus* — a simulation of the remaining
ECUs, a long-established practice in automotive integration testing. Both are
ordinary `provides` ports under this design, so the same forward machinery
serves them and no medium-specific mechanism is required. Integrating a
concrete restbus is future work (see Future Possibilities).

One caveat is carried rather than hidden: for option 3 a radio medium in
Python is comfortable for advertising, pairing, and control while being an
open question for sustained A2DP-class traffic, which compounds rather than
relieves the latency risk already recorded; options 1 and 2 keep the medium
in native C++ and avoid that entirely.

**Verified against real CVDs (2026-09-01).** Options 1 and 2 were both
exercised by hand on a single-node kind cluster with two Cuttlefish exporter
Pods — an `aosp_cf_x86_64_auto` head unit in Pod A and an
`aosp_cf_x86_64_only_phone` in Pod B (AOSP build 16102939, cuttlefish host
package 1.55.1). No Jumpstarter code was involved: the forward endpoint was
played by a 50-line asyncio TCP relay inside each Pod, and Pod-to-Pod
traffic went over the Pod network directly, which is what DD-4's Direct mode
will do. The router path is therefore still unmeasured; everything below is
about whether the *ports* behave when the other end is in another Pod.

Both options produced the same result, driven purely through `adb` and
`uiautomator`: BR/EDR inquiry lists the phone on the head unit, SSP numeric
comparison shows the same passkey on both screens, the bond completes with a
16-byte link key, and HFP, A2DP and AVRCP connect. Under option 2 the phone
then streamed a ringtone to the head unit over A2DP for ~38 s at ~42 KB/s
on the federated link, with no disconnect. Boot-to-bond is about two minutes
of wall clock, most of it guest boot.

*Option 2 — link-layer federation.* Both CVDs launched with
`--netsim_bt=false`, so each Pod runs its own rootcanal. Standalone rootcanal
binds **all four** ports (`hci`, `link`, `test`, `link_ble`) on `0.0.0.0`,
so the join needed no relay at all: from Pod B's test channel,
`add_remote <A> 7400 BR_EDR` and `add_remote <A> 7600 LOW_ENERGY` produced a
`link_layer_socket_device` on the BR/EDR and LE phys of *both* models. Three
things the flag names do not tell you:

- **BD_ADDR collision.** Every standalone rootcanal numbers its first HCI
  device `da:4c:10:de:00:00`, so two federated CVDs start with the same
  address and cannot pair. The fix used was `set_device_address` on B's test
  channel followed by a Bluetooth off/on in the guest so the stack re-reads
  its address; `--rootcanal_default_commands_file` is the launch-time
  equivalent. Under option 1 a single model numbers every device and the
  problem does not arise.
- **The join is an action, not a wiring.** `add_remote` is a runtime
  test-channel command that dials outward, so option 2 needs *two* forwarded
  ports (BR/EDR and LE link) plus a control step on the `requires` side
  after the forward is up — a natural post-establish hook for a rootcanal
  driver, but not something a static `forwards[]` entry expresses alone.
  Standalone rootcanal is also not the Cuttlefish default; netsim is.
- **Beacons leak.** The remote model's two default LE beacons appear in the
  peer's scan results, because the LE phys are joined wholesale.

*Option 1 — shared medium.* Pod A kept the default (netsim-backed) radio;
Pod B launched with `--netsim_bt=false --rootcanal_instance_num=2`, whose
effect is that B starts no controller at all and its `tcp_connector` dials
`127.0.0.1:7301` for HCI. A relay listening on B's `7301` and delivering to
A's `7300` made netsim in Pod A list both chips in `/v1/devices`. Two facts
matter for the design: netsimd binds its HCI port on **loopback only**, so a
forward endpoint *inside* Pod A is required rather than merely tidy (which
the design already provides); and the guest dials at boot, so the forward
must be up before the second CVD launches — a pre-launch ordering constraint
where option 2 has a post-launch one. The entire discovery-pairing-profile
flow moved ~140 KB phone→controller and ~13 KB back.

*Both.* crosvm's minijail sandbox cannot mount `/dev` inside a Pod, so both
launches needed `--enable_sandbox=false`; this is a JEP-0016 deployment
detail, recorded here because it cost the most time. Neither experiment
changes the decision — option 2 stays the Phase 1 default on its symmetry —
but the address and join findings above are why the Phase 1 driver work is
"a rootcanal control hook", not "nothing".

**Verified again with a GMS phone and the merged `bt-peer` driver
(2026-09-01, second pass).** The same two Pods, with Pod B now running the
GMS GSI phone of DD-10 instead of the AOSP phone, and the peer role played
by real Jumpstarter code for the first time.

*Regular pairing with a GMS phone.* The UI-driven flow — inquiry, SSP
numeric comparison, bond — succeeded as before, then stalled: no profile
connected, the head unit logged `CachedBluetoothDevice: No profiles`, and
the ACL dropped on `l2c_link_timeout` after SDP. The medium was not the
cause. The GSI enables only the LE-audio `bluetooth.profile.*` properties;
the classic set — A2DP source, AVRCP target, HFP AG, MAP and PBAP server,
PAN, HID, OPP — is product configuration in `/system/build.prop` (the AAOS
image carries its own sink-side set there), and the GSI's `system.img`
replaced it. Injecting the phone-side set into the GSI's `build.prop`, the
same in-place ext4 edit as the ADB fix, produced the full stack: A2DP sink,
AVRCP controller with browsing and BIP cover art, HFP client carrying the
phone's network and subscriber indicators, and PBAP client pulling the
phonebook over L2CAP, all `Connected`. YouTube Music on the phone then
streamed AAC 44.1 kHz stereo to the head unit over the federated link, and
the phone reconnected on its own after a Bluetooth off/on. Which profiles a
bench has is decided by the phone image, which makes this a second
image-preparation duty for the `cuttlefish` phone driver next to the ADB
one (*Reference drivers for projection*).

*`jumpstarter-driver-bt-peer` as the `requires` side.* The driver's
`BtPeer` class was run unchanged with
`transport: "tcp-client:127.0.0.1:17300"`, where `17300` was a
`kubectl port-forward` to Pod A's rootcanal `hci_port` — byte for byte what
a `bt=headunit.rootcanal:phone.controller` forward delivers, with the peer
end outside the cluster. rootcanal attached it as a second HCI device on the
head unit's own medium; the head unit discovered *Bumble-Phone*, SSP-paired
it (bond, encrypted ACL), listed it *Connected* with Media active, and
opened AVDTP to the peer's SBC source (`avdtp_connected` in the driver's
event log). That is the claim under *Ports* — "`bt-peer`'s Python is
untouched" — demonstrated on the merged driver. It exercises Bumble as a
*host*; the open question about Bumble as the *controller* (option 3) is
unchanged.

*Three constraints that change the driver's duties.*

- **The test channel is fragile, and the guest does not recover.** A
  latency probe that opened and closed rootcanal's `test_port` twenty times
  aborted rootcanal in *both* Pods — `test_channel_transport.cc: Check
  failed: written == size, errno = 32` in `SendResponse`: a client that
  closes before the banner is written is fatal. `process_restarter`
  respawned rootcanal within a second, but the guest's side of the HCI link
  (`tcp_connector`) took `SIGPIPE`, is not under the restarter, and the
  guest's Bluetooth sat in `BLE_TURNING_ON` until `cvd restart` (about
  20 s; userdata, bonds and installed apps survive it, where `cvd rm`
  discards them). So: the test channel is never a `provides` port — the
  rootcanal driver keeps it private and forwards only the HCI and link
  ports; a forward endpoint must not verify liveness by connect-and-close,
  which is why forward state comes from the splice and not from probing;
  and a controller crash on one exporter strands every peer's guest with a
  dead controller while the lease still looks bound, so the driver watches
  its controller and reports the forward `Failed` and the lease `Degraded`
  rather than leaving a bench that cannot pair.
- **Addresses follow attachment order, and numbers are reused.** rootcanal
  names the *n*-th live HCI device `da:4c:10:de:00:<n>` per model, so the
  Bumble peer took `:00:01` on attaching — the address the federated phone
  already held. The BD_ADDR finding above therefore generalises from "two
  CVDs" to "any host that attaches": the rootcanal driver assigns a
  per-member address (from the member index, say) with `set_device_address`
  in its post-establish hook, and before the host powers on, because SC
  pairing binds the address into the key derivation.
- **Every Cuttlefish guest has the same address plan.** Both instances came
  up with mobile data at `192.168.97.2`, Ethernet on `192.168.98.0/24` and
  Wi-Fi on `192.168.99.0/25` behind an AP built from the same OpenWrt
  rootfs. An L4 forward never sees this; anything that bridges guests at L2
  or L3 — the Phase 4 frame bridge, a Wi-Fi Direct emulation — must NAT or
  re-address, one more reason DD-10's option 3 goes first. Pod-to-Pod TCP
  connect on this single-node cluster was ~0.1 ms median; cross-node is
  still unmeasured. And because standalone rootcanal binds every port on
  `0.0.0.0`, it is the cluster's network policy, not the simulator, that
  keeps another Pod from `add_remote`-ing into a bench (*Security*).

The Phase 1 rootcanal hook is therefore three concrete things: a private
test channel, per-member addresses, and a controller watch.

**Verified over a real router forward, with Bumble as the phone
(2026-09-02, third pass).** The second pass reached rootcanal through a
`kubectl port-forward`; this pass replaced it with Jumpstarter's own data
path and removed the phone CVD entirely. Three Pods: the head unit's Pod
gained a second, hook-less exporter identity that exports its rootcanal
`hci_port` as a `TcpNetwork` (plus an echo port, for measurement); a bt-peer
Pod ran the merged driver with `transport: "tcp-client:127.0.0.1:7300"`; and
a sidecar in that Pod held a lease on the rootcanal exporter and published
both ports locally with `TcpPortforwardAdapter` — a hand-rolled stand-in for
the forward endpoint this JEP specifies, with the same shape: one lease, a
listener on the `requires` side, `RouterService.Stream` in the middle. Every
HCI byte the peer sent crossed bt-peer Pod → router → head unit Pod, and the
head unit paired with it: discovered *Bumble-Phone*, SSP-bonded, encrypted
ACL, listed `Connected` with **Phone and Media both on**.

*A phone, not just a headset.* The merged `BtPeer` presents an A2DP source,
which lights Media. Subclassing it to add an HFP **Audio Gateway** — a
`bumble.rfcomm` server, `hfp.AgProtocol` with the call/callsetup/callheld,
service, signal, roam and battery indicators, and `hfp.make_ag_sdp_records`
in the device's SDP database — lit Phone as well: `hfp_slc_complete`,
codecs `CVSD`/`MSBC` negotiated, `avdtp_connected`, and the head unit's
`HeadsetClientStateMachine` `Connected`. Ringing the head unit from the peer
(`callsetup=1` + `RING` + `+CLIP`) put a call into AAOS Telecom —
`SET_RINGING (successful incoming call)` against
`HfpClientConnectionService`, phone account `HFP …:00:01`. So the phone-facing
half of a head-unit bench needs no second guest at all: the cheapest bench
is one CVD and a Python process, and the `bt-peer` driver should grow a
`profiles:` config (`a2dp-source`, `hfp-ag`, later AVRCP target and PBAP
server) rather than a second driver.

*What the router costs.* On the same endpoint, from the same Pod: a
round-trip through the forward was **0.70 ms median** (p90 0.91 ms, n=100)
against **0.05 ms** direct Pod-to-Pod — about 0.65 ms of router. An HCI
command round-trip to rootcanal was **~45 ms median through the forward and
~45 ms direct**: the simulator's own scheduling dominates by two orders of
magnitude, and the router is ~1.5% of an HCI exchange. Simulated Bluetooth
pairing therefore cannot tell the two transports apart — which is what makes
DD-4's preference for a direct in-cluster connection free rather than a
gamble, since falling back to the router is undetectable on this path. The
*Latency characterization* test exists to find where that stops being true
(A2DP-class throughput, cross-node, physical controllers, and the Phase 4
frame bridge, where it is expected to).

*Three operational constraints, all in the forward endpoint's lap.*

- **An ingress reload cuts every long-lived stream, and nothing re-dials.**
  This cluster's nginx ingress reloads on any `Ingress` change — 85 times in
  one log — and each reload drains its old workers with
  `worker_shutdown_timeout 240s`. The arithmetic is visible end to end: a
  reload at 04:00:13, the exporter's controller status stream cut at
  04:04:13.6, the forwarded HCI splice broken at 04:04:55, and the client's
  next driver call failing `UNAVAILABLE: Socket closed`. The exporter
  reconnected its own status stream in half a second; the *forward* did not,
  so the peer's HCI transport stayed dead and the bench looked bound while
  the simulated phone was off the air. Immediately after such an event the
  first new connection to the listener is also reset, and the next one
  succeeds. Nothing about this is Bluetooth-specific or exotic — any
  cluster whose proxy reloads has it — so reconnection belongs to the
  forward endpoint, not to each driver: the `requires` side re-dials its
  peer stream and re-splices transparently, because a driver that opens its
  transport once at start, as `bt-peer` and every HCI or serial-style client
  does, has no way to notice or recover. Drivers that genuinely must know —
  the head unit server of DD-10 — get the reset as an event, which is the
  same rule *Reference drivers for projection* already states.
- **One identity, one process.** Running the exporter twice under the same
  identity — trivially, a Deployment scaled to two — leaves both registered;
  connections then land on whichever process the router picks, and the one
  that does not own the lease's session answers `RouterService.Stream` with
  a `KeyError` on the driver UUID and closes. It cost an hour of chasing a
  "flaky tunnel". A member exporter is single-writer: the deployment runs
  one replica, and a duplicate registration is worth detecting rather than
  tolerating.
- **Both ends must be the same build.** A forward endpoint running a newer
  `jumpstarter-driver-network` than the exporter's runtime accepted
  connections and returned EOF on the first byte, with no error on either
  side. Version skew between the two halves of a splice is silent, which is
  an argument for the forward endpoint being the exporter's own code —
  as specified — rather than an arbitrary client-side helper.

Two smaller notes for whoever writes the tests. Bumble's bonds live in
memory unless a keystore is configured, so restarting the peer invalidates
the DUT's stored link key and the DUT must forget the bond before re-pairing
— the driver should persist its keystore per lease, or the test should
forget first. And deleting a `Lease` object out from under a waiting client
leaves that client retrying `not found` forever instead of re-queueing:
leases are released, not deleted (*Lease state*).

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
Running it changed that judgment. With the two Pods of the DD-9 experiment,
a GMS phone image and Google's own receiver projected a live Android Auto
session across a single forwarded port (the verification below). Version
negotiation, the TLS authentication between GMS and the receiver library,
service discovery, the phone-side first-run flow, and the rendered launcher
with video, audio and input all ran unchanged. That is not a proxy check;
it is the whole projection stack running on two exporters.

What option 3 does not exercise is precisely bounded: the **handover** —
the credential exchange over Bluetooth, the phone joining the head unit's
Wi-Fi Direct group — and the radio-layer failure modes (RSSI, roaming,
channel loss). Those are the reasons the medium must eventually be
simulated, and they are all that Phase 4 is for. Everything downstream of
the handover runs over the forward, so a lab gets a real projection
workload on day one, in CI, on hardware-free nodes, and Phase 4 arrives as
a fidelity upgrade rather than as the first time anything projects.

Two facts from the same experiment shape how the medium options are built:

- **The Cuttlefish Wi-Fi medium is not a byte stream.** The guest's
  `virtio_mac80211_hwsim` feeds a per-environment `wmediumd` over a
  **vhost-user** Unix socket (shared memory plus fd passing), with an
  OpenWrt VM as the access point. `--vhost_user_mac80211_hwsim` is
  Cuttlefish's documented way to share one medium between instances, but
  only on one host. Across hosts, option 1 is therefore a *bridge* — a
  component on each exporter that terminates vhost-user locally and
  exchanges 802.11 frames with its peer — and the forward is only the pipe
  between the two bridges. This is why option 1 is the general answer and
  also the one that needs new code and datagram semantics (Future
  Possibilities).
- **The guest's Wi-Fi is already IP-reachable.** Each CVD's guest joins its
  own OpenWrt AP and reaches the exporter's network through the AP's WAN
  link; with one host route and one forwarding rule on the AP, the exporter
  reaches the guest's Wi-Fi address in turn. So a Cuttlefish driver can
  expose a guest-side port two ways: through `adb forward`, which models
  the USB cable, or through the guest's `wlan0` address, which models the
  Wi-Fi link. The projection session was run both ways with the same
  result. They are two `provides` ports, not two mechanisms, and which one
  a test forwards is a topology choice DD-13 leaves to the test.

Between 1 and 2, option 2 is far cheaper when it applies, because it reuses
the same forward machinery as Bluetooth and netsim already owns the frames.
Option 1 is the general answer, because `wmediumd` is where RSSI and
delivery modeling live. Option 1 is also the hardest thing in this JEP and
the most likely to need upstream work — it is scheduled last and its risk
is called out explicitly.

**Verified against real CVDs (2026-09-01).** Same two Pods, same stand-in
relays, same Direct-mode caveat as DD-9. The pieces, all public:

- *Phone.* Google's GSI with GMS for x86-64 (`gsi_gms_x86_64`, Android 17,
  a `user` build) dropped into the Cuttlefish AOSP image set: `super.img`
  rebuilt with the GSI `system.img` over the AOSP vendor partitions, and the
  GSI `vbmeta.img` (verification disabled) in place of Cuttlefish's. Being a
  `user` build it boots with `adbd` stopped; enabling it meant editing the
  ext4 `system.img` in place (`persist.sys.usb.config=adb` in `build.prop`,
  the exporter's public key in `/adb_keys`). Android Auto 17.4 (x86-64
  split APKs) was then sideloaded and its developer-mode *head unit server*
  started on port 5277 — the port the phone `provides`.
- *Head unit.* Google's Desktop Head Unit 2.1 in the AAOS Pod, under Xvfb,
  in its `--adb=5277` mode, which is a plain TCP client. This is the only
  publicly available Android Auto receiver: the AAOS CVD cannot receive
  projection because the receiver ships with GAS, so for a virtual bench
  the head unit's projection endpoint is a process inside the head-unit
  exporter that `requires` the phone's port (see *A projection bench,
  concretely*). Direction falls out of DD-13 as expected.
- *Forward.* DHU → `127.0.0.1:5277` in Pod A → Pod B → the phone, first via
  `adb forward tcp:5277` and then via the guest's `wlan0` address through
  the OpenWrt AP.

The session negotiated protocol 1.7, completed TLS 1.2
(ECDHE-RSA-AES128-GCM-SHA256 — the relay must be byte-transparent), ran
service discovery, walked the phone-side first-run flow (consent, car
authorization, notification access — each driven by `uiautomator`), and
rendered the full launcher with Maps, YouTube Music and the phone app live.
Video is phone→head-unit and was about 0.6 MB for three minutes of a mostly
static screen, so the L4 path is not bandwidth-limited in Direct mode.
Three operational findings are folded into *Reference drivers for
projection* under Design Details: the DHU quits on stdin EOF and needs a
display; Android Auto's head unit server does not survive an aborted
session (`IllegalStateException: Already connected`); and the GSI phone
would not join Wi-Fi at all while it held a validated Ethernet network.

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

**Rationale:** These two kinds of inference look similar and are opposites,
because they sit on different sides of the line DD-6 and DD-10 already drew.

Direction is a **property of the drivers**, not a choice. Whether rootcanal is
the listening end is a fact about how rootcanal works, already declared in the
exporter's report and already validated by the controller. Making the author
write it down asks them to know exporter internals — exactly what DD-6
removed addresses to avoid. Worse, it is knowledge that can go stale: a lab
that swaps a `provides` implementation for one that dials would silently
invalidate every lease manifest naming it as `from`. Inferring direction is
therefore not a convenience but a correctness improvement, and it costs
nothing — the controller already checks the reported roles, so option 1 uses
that check to *assign* rather than merely to *reject*.

Topology is a **property of the test**. DD-10 makes this concrete: the
same phone exporter is a USB-attached phone when a test forwards `aa-hu`
and a wireless one when it forwards `aa-hu-wifi`, and two exporters are a
bench in one test and independent devices in another, so the wiring belongs
to whoever wrote the test. Option 2 makes it a
property of whatever drivers happen to be configured on whichever exporters
the selectors happened to bind, which fails in three ways:

- **Nondeterminism across bindings.** A selector-based member binds to
  different exporters on different runs. If those exporters declare different
  ports, the bench wires itself differently run to run — the worst property a
  reproducible test can have.
- **Ambiguity is not rare.** Two members each exposing several ports turns
  matching into a bipartite matching problem — the same one ATS's
  `AdhocTestbedSchedulingUtil` solves for device allocation. Tractable, but a
  silently wrong wiring is worse than an error.
- **It weakens a security property.** This JEP states that a forward is not a
  general exporter-to-exporter tunnel, because an exporter can reach only a
  peer *a lease it is bound to explicitly names*. Option 2 relaxes "explicitly
  names" to "happened to match", and an unintended pairing of two `console`
  ports is a data path nobody requested.

Option 3 remains available as `from`/`to` for the case where wiring should be
pinned regardless of what the exporters report, and the controller then checks
the stated roles against the reported ones rather than assigning them.

The ergonomic complaint that motivates option 2 — `member.port:member.port`
is verbose — is better answered in the client. The CLI expands shorthand into
an explicit `spec.forwards[]` entry before submission, so the stored object
stays auditable and `kubectl get lease -o yaml` shows the real wiring. Where a
lab adopts the same port name on both sides, `--forward bt` can expand to it;
that is a naming convention worth offering and not worth depending on.

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
was noticed, so it is stated rather than assumed:

> **Each exporter identity is served by exactly one process.**

Two processes sharing one identity both register and both look healthy; the
controller hands a lease to one of them, and connections that the router
routes to the other are answered with a `KeyError` on a driver UUID that
process has never heard of, closing the stream with no useful error on
either side. The symptom presents as a flaky forward, not as a
misconfiguration. A member exporter therefore runs one replica, and a second
registration under a live identity is worth detecting and refusing rather
than tolerating.

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

**Direct eligibility.** The controller offers `prefer_direct` when both
bound members report the same non-empty `NetworkZone` and the `provides`
side reports a `PeerEndpoint` (DD-4). Zone is deliberately opaque: an
in-cluster exporter takes it from the deployment — one value per cluster
network, which JEP-0016's provisioner sets for every Pod it renders — and an
edge exporter that reports none is never a direct candidate, so it keeps the
router path without any per-lease configuration. This keeps reachability a
statement someone made about the deployment rather than something the
controller infers from addresses it cannot test, which is the same reasoning
DD-13 applies to topology. A pair that claims a shared zone but cannot
actually connect is not a failure mode the user sees: the dial times out and
the router stream, already minted, carries the forward.

**Reconnection belongs to the endpoint.** A forward outlives any single
stream: an ingress reload, a router restart, or a node's network blip cuts
the peer stream, and in a cluster whose proxy reloads on every unrelated
`Ingress` change that is a routine event, not an incident (DD-9, third
pass). The `requires` side therefore re-dials with backoff and re-splices
accepted connections without tearing down its listener, and the first
attempt immediately after a cut is expected to fail and be retried. The
alternative — surfacing the reset to the driver — does not work, because the
drivers this JEP is for open their transport once at start: `bt-peer` dials
its HCI port when the peer is created, and after a silent cut the bench
still reports `Ready` while the simulated device is off the air. Drivers
that must know still learn of it: a reconnect is an event on the forward
(*Observability*), which is how the head unit server of DD-10 gets its
mandatory restart. Forward state comes from the splice, never from probing
the far end (DD-9, second pass).

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
| Ingress/proxy reload cuts the peer stream | `requires` side re-dials and re-splices transparently; the first attempt after the cut is expected to fail. Observed in DD-9's third pass, and invisible to drivers that dial once |
| Direct dial fails or times out | Fall back to the already-minted router stream; recorded as a metric, and conspicuous for a same-zone pair that should have connected |
| A member's exporter disappears | Peer's stream resets; lease `Degraded` naming the role (DD-3) |
| Client releases the lease | Forwards torn down first, then the lease ends normally |

### Reference drivers for projection

Nothing in the projection bench needs new forward machinery, but the
verification (DD-10) showed that the two reference drivers own real work,
of the same kind as DD-9's rootcanal control hook:

- **`cuttlefish` (phone).** A GMS GSI is a `user` build: `adbd` is off and
  stays off, so the image the driver boots must carry
  `persist.sys.usb.config=adb` and the exporter's ADB public key in
  `/adb_keys`. The same image decides which Bluetooth profiles the phone
  offers — a GSI ships only the LE-audio `bluetooth.profile.*` properties,
  and the classic phone-side set has to be added to `/system/build.prop`
  (DD-9, second pass). Both are image-preparation steps the driver
  documents and JEP-0016's provisioner can run once per image, not
  per-lease actions. Recovery is `cvd restart`, which keeps userdata —
  bonds and sideloaded apps included — where `cvd rm` does not.
  For `aa-hu-wifi` the driver brings the guest onto the instance's OpenWrt
  AP, adds the host route to the AP's LAN and the `wan→wifi` forwarding
  rule on the AP — and cuts the guest's Ethernet first, because Android
  never asks Wi-Fi to connect while it holds a validated Ethernet default.
  The head unit server is started on request and **restarted whenever the
  forward resets**: Android Auto does not survive an aborted session, so a
  forward reconnect must reach the driver as an event, not be hidden in the
  splice.
- **`dhu` (head unit).** Runs the Desktop Head Unit as a process: an X
  display (Xvfb), a dummy audio driver, stdin held open — the DHU exits on
  stdin EOF because stdin is its interactive console, which is also the
  head-unit-side stimulus API (key presses, day/night, microphone) the
  driver exposes. Screenshots come from the display. The DHU dials the
  instant it starts, so the driver starts it only on a client call, after
  the forward is Ready, which is the same ordering rule `bt-peer` already
  follows.
- **`bt-peer` (phone).** The third pass of DD-9 showed the driver is one
  config away from being a phone rather than an audio source: with an HFP
  Audio Gateway alongside its A2DP source it satisfies both of a head unit's
  phone-facing profiles, and can ring it. That belongs in the driver as a
  `profiles:` list, with the bond keystore persisted for the life of the
  lease so a peer restart does not strand the DUT's link key.

The `cuttlefish` and `dhu` drivers are the reference `requires`/`provides`
pair for projection.
What they must not do is know about each other: the phone driver exposes
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
  models, and — by accident — crash the controller (DD-9, second pass). A
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
  association. `jumpstarter-driver-netsim` supplies the pcap capture used to
  evidence what actually crossed the air.
  Runnable in CI on KVM-capable nodes with no lab hardware. This is the
  headline result and should run on every merge once it exists.
- **Virtual ↔ peer**: one `jumpstarter-driver-cuttlefish` exporter and one
  `jumpstarter-driver-bt-peer` exporter, joined by
  `bt=headunit.rootcanal:phone.controller`. Assert the CVD discovers and
  pairs the peer and the driver reports `avdtp_connected` — the smallest
  bench, no second guest to boot. With the peer's HFP AG enabled, assert the
  head unit's `HeadsetClientConnectionService` takes a ringing call from it,
  which covers the phone-facing profiles without a phone. DD-9's third pass
  ran exactly this by hand, over the router. Cheapest CI tier; runs on every
  merge.
- **Virtual projection**: a GMS-GSI phone CVD and a `dhu` exporter in
  separate Pods, joined by the `projection` forward over `aa-hu-wifi`
  (DD-10). Assert the receiver reaches the launcher and a screenshot of the
  head-unit display matches. Same CI tier as the Bluetooth test; the two
  compose into one bench once Phase 1 and Phase 2 are both green.
- **Physical ↔ physical**: a physical phone and head unit on two exporters
  **on different lab hosts** — the allocation ATS's `SimpleScheduler`
  refuses. Requires lab hardware; runs on a labeled runner.
- **Latency characterization**: HCI round-trip through a router forward vs. a
  direct forward vs. host-local rootcanal, reported as a distribution. Its
  result determines whether A2DP-class workloads are in scope for router mode.
  The by-hand version (DD-9, third pass) found the simulator, not the
  transport, to be the cost on a single node; the test exists to find where
  that inverts — cross-node, sustained A2DP, and physical controllers.
- **Forward resilience**: cut the peer stream of a live bench (restart the
  router, or reload the ingress in front of it) and assert the forward
  re-establishes, the reconnect is counted and emitted, and a driver that
  dialed its transport once at start is still talking to the far end
  afterwards. Runs in the same CI tier as the Bluetooth bench.

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
- [ ] **Phase 2 — Virtual projection**: a GMS phone CVD and a `dhu` head
      unit in separate Pods complete an Android Auto session to the
      launcher over one forwarded port, in CI, with no lab hardware.
      *Demonstrated by hand on 2026-09-01 over both `aa-hu` and
      `aa-hu-wifi` with a stand-in relay (DD-10); the drivers, the router
      path and CI remain.*
- [ ] **Phase 3 — Physical ↔ physical across hosts**: a phone and head unit
      on exporters on different lab hosts complete a phone projection
      session (Android Auto in the reference implementation)
- [ ] **Phase 4 — Virtual Wi-Fi medium**: two CVDs in separate Pods
      associate over a bridged `mac80211_hwsim`/`wmediumd` medium or a
      shared netsim 802.11 chip, and a projection session completes the
      Bluetooth → Wi-Fi handover end to end
- [ ] Measured HCI round-trip latency through a router forward is published,
      with a documented statement of which workloads it does and does not
      support. *First data point, 2026-09-02: 0.65 ms of router against a
      ~45 ms rootcanal command round-trip, single node (DD-9, third pass);
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
- Homogeneous benches are the tractable half of the problem and already
  deliver both headline results; mixing physical and virtual devices in one
  bench needs no new software mechanism, only radio hardware (DD-11).
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
- **The projection bench depends on Google artifacts this project cannot
  ship.** The GMS GSI and the Desktop Head Unit are published by Google
  under their own terms; the Android Auto APK is not published outside
  Google Play, and the verification sideloaded a mirror copy. A lab must
  supply these itself, and CI for Phase 2 needs an artifact path that does
  not redistribute them. Mitigation: the drivers take image and APK
  locations as configuration, the reference documents where each artifact
  comes from, and the Bluetooth phase carries the CI headline on AOSP-only
  images.
- **Bluetooth timing may be tighter than measured** — pairing may work while
  A2DP streaming does not. Mitigation: latency characterization is an
  acceptance criterion whose answer is published, not assumed.
- **The simulators are less robust than a lab needs.** rootcanal aborts
  when a test-channel client disconnects early, its restart does not
  reattach the guest, and a crash on one exporter strands every bench
  member's controller (DD-9, second pass). Mitigation: the rootcanal
  driver keeps the test channel private, never probes, watches its
  controller and reports `Degraded`, and owns `cvd restart` as the recovery
  action; the abort itself is an upstream fix worth contributing.
- **The cluster data path is less stable than a bench assumes.** Long-lived
  streams do not survive ordinary cluster events: an nginx ingress reloads
  on any `Ingress` change and drains its workers on a timer, cutting every
  gRPC stream through it — measured on the verification cluster as a reload
  at 04:00:13 and the exporter's controller stream cut at 04:04:13, with
  the forwarded HCI splice going down 40 s later (DD-9, third pass). A
  multi-hour bench will meet this repeatedly. Mitigation: reconnection is
  the forward endpoint's responsibility, reconnects are observable, and the
  HIL suite includes a bench that survives a deliberate stream cut. An
  in-cluster bench also avoids the machinery altogether, which is part of
  why `Auto` prefers a direct connection there (DD-4). A
  related hazard is version skew across a splice — a newer client-side
  network driver against an older exporter closed every forwarded
  connection at the first byte with no error — which is an argument for the
  endpoint being the exporter's own code, as specified.
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

- **Bumble as a Cuttlefish controller.** DD-9's options 1 and 2 are now
  verified against real CVDs; option 3 is not. Pointing a CVD at a Bumble
  `mode=controller` endpoint is documented for the Android emulator but not
  for Cuttlefish, and it is the only option that reaches beyond Cuttlefish.
  One more prototype run settles it.
- **Who issues the link-layer join, and who owns addresses?** The
  federation experiment showed that option 2 needs a control step
  (`add_remote` on the test channel) after the forward is up, and that
  standalone rootcanals collide on `da:4c:10:de:00:00` unless one is
  re-addressed — and the second pass showed any attaching host collides
  too, since numbering is per model and reused. The candidates are a
  post-establish hook on the `requires`
  side driver, a lease-level `forwards[].onEstablish` action, or leaving it
  to the test. The first keeps the controller ignorant of Bluetooth, which
  DD-8 and DD-13 argue for.
- **Forward-before-launch ordering.** Under option 1 the guest dials its HCI
  port at boot, so the forward must exist before the second CVD is created;
  under option 2 the join must happen after. The JEP-0016 provisioner creates
  the CVD at lease time, so the forward has to be established in the window
  between binding and `cvd create`. Whether that is a lease-status condition
  the provisioner waits on, or a retrying connector, is open.
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
  force a fresh pairing; likely protocol-specific. Two data points so far:
  Android Auto's head unit server must be restarted after a dropped session,
  so the reconnect has to be visible to the endpoint drivers; and a Bumble
  peer whose transport is cut loses its in-memory bonds, so the DUT's stored
  link key is stale and pairing has to be redone unless the driver persists
  its keystore for the life of the lease. The Bluetooth answer may simply be
  "persist the keystore and re-attach", which would make reconnect
  invisible for that medium.
- How Phase 2's CI obtains the GMS GSI, the Desktop Head Unit and the
  Android Auto APK without redistributing them (see Risks).
- How `jmp get leases` renders N exporters in a table column readably.

## Future Possibilities

Not part of this proposal:

- **Mixed physical/virtual benches** (DD-11) — a real device paired to a
  virtual one through a gateway exporter owning a real radio adapter. This
  needs no new software mechanism: the adapter is an ordinary `provides`
  port and the lease plane is unchanged. What it needs is lab hardware near
  the physical device, and a decision about how to model a shared RF
  resource — as a member with its own role, which makes it schedulable and
  auditable but turns a two-device bench into three members, or as an
  attribute of the physical member's exporter.
- **Selection-time port validation** via JEP-0015 dynamic exporter labels, so
  a bench whose ports cannot be satisfied reports `Unsatisfiable` without
  holding anything (DD-12).
- **A global lease scheduler** with a view of all leases and exporters, which
  the controller already carries a `TODO` for; it would close the binding race
  for single- and multi-member leases alike.
- **Fan-out forwards** — one `provides` port serving several `requires`
  ports, for shared media with more than two participants. Today's forward is
  strictly point-to-point. Bumble's link relay already implements the
  underlying idea: a WebSocket relay hosting virtual *rooms*, each room a set
  of virtual controllers that can advertise and exchange ACL data with one
  another. A room is the N-way medium DD-6 deferred, so this is an
  integration rather than an invention.
- **Ephemeral `listen` allocation** for `requires` ports. Only needed if the
  one-exporter-per-namespace assumption is relaxed; it trades the fixed
  address for driver coupling, so it is not worth doing while the assumption
  holds.
- **Bench-level access policy and quota** (DD-12).
- **Per-member early release**, if the all-or-nothing lifetime proves coarse.
- **Datagram forwards.** `wmediumd` and other frame-oriented media want
  datagram semantics with real boundaries and no head-of-line blocking; the
  additive `FRAME_TYPE_DATAGRAM` extension to `RouterService.Stream` (and,
  further out, QUIC unreliable datagrams) is a separate protocol JEP, for
  which Phase 4 is the most compelling justification. The frame bridge
  DD-10 describes is the consumer: it terminates vhost-user on each
  exporter and needs a datagram pipe between the two halves.
- **Vehicle-bus counterparty integration.** The restbus pattern described in
  DD-9 has mature tooling behind it, and a broker that exposes CAN, LIN,
  FlexRay, and Automotive Ethernet over a gRPC socket is already a `provides`
  port needing nothing new from this JEP. RemotiveLabs is the obvious
  candidate — RemotiveBroker plus RemotiveTopology's DBC/ARXML-driven restbus
  — and it composes with the automotive drivers Jumpstarter already ships
  (`can`, `doip`, `someip`, `uds`, `xcp`, `obd`) as the counterparty they
  talk to rather than a replacement for any of them. Their AAOS emulator
  integration, which feeds VHAL from broker signals, suggests a three-member
  bench worth building eventually: a virtual head unit driven by real vehicle
  signals, a restbus supplying the rest of the vehicle, and a phone for
  projection, with every pairwise connection an ordinary forward. **This is
  deliberately out of scope here and should get its own JEP** — the broker is
  a commercial product, so a driver is an integration against something the
  user licenses rather than a dependency this project can ship, and that
  distinction deserves its own design discussion rather than a line in this
  one's acceptance criteria.
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
- 2026-09-01: DD-9 options 1 and 2 verified by hand with two CVD Pods on a
  kind cluster (pairing, HFP/A2DP/AVRCP, A2DP streaming); findings recorded
  in DD-9 and Unresolved Questions
- 2026-09-01: DD-10 option 3 verified by hand: Android Auto 17.4 on a GMS
  GSI phone CVD projected to the Desktop Head Unit in the other Pod over a
  single forwarded port, then again with the phone end on its Wi-Fi NIC via
  the per-instance OpenWrt AP
- 2026-09-01: DD-10 decision revised on that evidence — L4 projection
  promoted from diagnostic to the Phase 2 deliverable, the Wi-Fi medium
  reframed as a frame bridge; projection bench, `aa-hu`/`aa-hu-wifi` ports,
  `dhu` driver and phase renumbering (1–4) added
- 2026-09-01: DD-9 second pass — regular pairing and the full classic
  profile stack (plus A2DP streaming) between the GMS GSI phone and the
  AAOS CVD over federated rootcanals, after adding the phone-side
  `bluetooth.profile.*` set to the GSI; the merged
  `jumpstarter-driver-bt-peer` run unchanged as a `requires`-side host
  against a CVD's rootcanal through a port-forward; rootcanal test-channel
  crash, address reuse and the identical guest address plan recorded as
  driver duties, a Security bullet and a Risk
- 2026-09-02: DD-9 third pass — the same bench over Jumpstarter's own data
  path: a rootcanal `TcpNetwork` exporter, a bt-peer Pod, and a lease-holding
  forward endpoint splicing them through `RouterService.Stream`, with a
  Bumble HFP-AG + A2DP peer standing in for the phone (pairing, Phone and
  Media, a ringing call into AAOS Telecom). Router overhead measured at
  0.65 ms against ~45 ms of rootcanal; proxy-reload stream loss, duplicate
  registration and version skew recorded as forward-endpoint duties
- 2026-09-02: those findings made normative — DD-4's fast path given a
  measured price, a single-process invariant per exporter identity,
  reconnection assigned to the forward endpoint with reconnects as events, a
  cluster-data-path Risk, a *Forward resilience* HIL test, and two acceptance
  criteria
- 2026-09-02: Motivation gained *Why the exporter, not the device, is the
  unit of a lease* — harnessed multi-device benches are already one exporter
  and stay that way; the gap is physically separate benches on separate
  hosts, and the gain is gang-scheduling any combination of them
- 2026-09-02: `Auto` changed to **prefer a direct peer connection between
  same-zone members** rather than defaulting to the router — DD-4 rewritten,
  `PeerEndpoint`/`NetworkZone` added to `ExporterStatus`, `prefer_direct`
  added to `DialPeerResponse`, direct-first-with-fallback replacing the
  raced dial, and a *Direct eligibility* rule added to Design Details

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
