# JEP-0015: Multi-Exporter Leases and Inter-Exporter Port Forwarding

| Field             | Value                                                    |
| ----------------- | -------------------------------------------------------- |
| **JEP**           | 0015                                                     |
| **Title**         | Multi-Exporter Leases and Inter-Exporter Port Forwarding |
| **Author(s)**     | @kirkbrauer (Kirk Brauer, kbrauer@hatci.com)             |
| **Status**        | Discussion                                               |
| **Type**          | Standards Track                                          |
| **Created**       | 2026-09-01                                               |
| **Updated**       | 2026-09-26                                               |
| **Discussion**    | [PR #1069](https://github.com/jumpstarter-dev/jumpstarter/pull/1069) |
| **Requires**      | JEP-0014                                                 |
| **Supersedes**    |                                                          |
| **Superseded-By** |                                                          |

---

## Abstract

This JEP extends `Lease` to acquire multiple exporters together and to
forward socket traffic between their named driver ports. Optional
`spec.members[]` assigns roles to exporters, and `spec.forwards[]` declares
connections between them without exposing local addresses. All member claims
are committed in one status update, and the members share a lease lifetime.
Forwards reuse the existing port-forwarding primitives and router, with
mutually authenticated, encrypted direct connections preferred within a
network zone. The same lease serves virtual devices, where forwards carry
the simulated radio medium between Pods, and physical devices, which share
a real radio or cable while the lease coordinates their access.
Forwards carry socket traffic only. Ports that describe a physical cable or
radio are reported but cannot be forwarded (DD-14).

## Motivation

A Jumpstarter lease currently grants exclusive access to one exporter.
Tests involving several exporters must acquire separate leases, coordinate
their lifetimes, and arrange any connections between devices themselves.
If one request succeeds while another waits, a test holds hardware it cannot
use; concurrent tests can each hold a device the other needs.

An exporter remains the unit of allocation. It owns a DUT and its harness,
which may include several physically connected devices. Leasing those
devices separately could give different clients control of the same
assembly. This proposal instead joins independently managed exporters into
one lease. A pool of N phones and M head units can then
support N×M pairings without pre-wiring each pair.

Phone projection illustrates the need: a test must control both a phone and
a head unit, pair them over Bluetooth, and observe the handover to Wi-Fi.
The devices may run different operating systems and belong to exporters on
different hosts. Other examples include two ECUs testing a CAN gateway, a
BLE peripheral and its central, or a DUT and a serial companion.

Separate leases leave four gaps:

- **Acquisition:** there is no all-or-nothing claim across exporters.
- **Lifetime:** devices can expire or be released independently.
- **Policy and observability:** requests have no shared lease identity.
- **Connectivity:** existing streams connect clients to exporters, with no
  managed exporter-to-exporter path.

### Virtual and physical devices

Leases of virtual and physical devices use the same members, exclusivity,
and lifetime. They differ in where the medium between devices lives, and so
in what a forward carries:

| | Virtual devices | Physical devices |
| --- | --- | --- |
| **Example** | A Cuttlefish phone and head unit in separate Pods | A real phone and head unit on two lab hosts |
| **Medium between devices** | Simulator sockets on each exporter: rootcanal HCI, netsim, `wmediumd` | A real radio or cable |
| **What joins the devices** | A forward carries the simulated medium | The air or the harness; the lease coordinates access |
| **What forwards carry** | Bluetooth HCI and link layer; projection over ADB or the guest's Wi-Fi address | Only socket-bridged traffic, such as CAN through `socketcand` or a serial bridge |
| **Placement** | Anywhere reachable; forwards cross nodes | Within RF range or on one harness, chosen through exporter labels |
| **Goal** | Run multi-device tests on every change without lab hardware | Run the same tests on real stacks and radios |

A test written against roles can run against either kind of device; the
lease's selectors and forwards decide which. Virtual devices have one further
constraint: simulator interfaces are host-local. HCI over TCP can use a byte
forward, while interfaces such as vhost-user also need a local bridge (DD-9,
DD-10). A lease does not pair a virtual device with a physical radio
(DD-11). *Scope* below lists what is in and out.

### User Stories

- **As a** test engineer, **I want to** acquire a phone and a head unit in
  one lease and control each by role, **so that** my projection test never
  holds one device while waiting for the other, and I keep the surviving
  device for diagnostics if one fails.
- **As a** CI maintainer, **I want to** pair virtual devices running in
  separate Pods, **so that** projection sessions run on every change without
  physical phone or head unit hardware.
- **As a** validation engineer, **I want to** run the same projection test
  on a real phone and head unit within RF range of each other, **so that**
  real radios and vendor stacks are checked by the test that CI already
  runs.
- **As a** lab engineer, **I want to** connect exporters on different hosts,
  such as two ECUs through a CAN-over-TCP bridge, **so that** a test can use
  devices that are not wired to the same machine.
- **As a** lab administrator, **I want to** attach two exporters to one head
  unit, one for power and the serial console and one for cameras and CAN,
  **so that** a test can lease either or both while no other test can reach
  that head unit through the other exporter.

## Proposal

### Scope

This JEP adds four concepts to existing resources:

| Concept | Where | What it does |
| --- | --- | --- |
| **Members** | `Lease.spec.members[]` | Role names, each with the existing `selector` / `exporterRef`. A lease binds every required member or holds none. |
| **Ports** | Exporter config, reported in `DriverInstanceReport` | Named connection points a driver **provides** (a local service) or **requires** (a socket it dials). Addresses never leave the exporter. |
| **Forwards** | `Lease.spec.forwards[]` | Join a provided port on one member to a required port on another. Exporters establish them over the existing router, or directly within a network zone. |
| **Exclusion groups** | `Exporter.spec.exclusionGroup` | Set by an administrator on exporters that serve one DUT; at most one lease holds the group (DD-16). |

A lease with members appears in `jmp get leases` and uses the existing
expiry and `spec.release` behavior. Scalar leases are unchanged.

**In scope:**

- All-or-nothing binding of up to eight members, with per-member access
  policy (DD-1, DD-12).
- Exclusive access to a DUT that several exporters serve, through an
  admin-assigned exclusion group (DD-16).
- TCP byte forwards between two socket ports, with direction checks and an
  optional protocol tag (DD-4 to DD-8).
- Client, CLI, and `JumpstarterTest` support for member-form leases, and
  Mobly export.
- Reference topologies, delivered in order: Phase 1 virtual Bluetooth,
  Phase 2 virtual projection, Phase 3 physical devices across hosts, and
  Phase 4 virtual Wi-Fi.

**Not covered, and how this JEP behaves instead:**

| Not covered | Behavior in this JEP |
| --- | --- |
| Switching physical links (Ethernet, CAN, LIN, FlexRay) through relays, switches, or signal gateways | `wired` and `wireless` ports are reported; a forward that names one is `Invalid` (DD-14) |
| Choosing co-located devices during binding | Placement comes from exporter labels in member selectors |
| Shared power supplies and other devices serving several exporters | Not modeled |
| Pairing a virtual device with a physical radio | Not supported (DD-11) |
| Datagram transport and forwards with more than two endpoints | TCP byte streams between exactly two ports |
| Rejecting unsuitable exporters during selection | Ports are validated after binding (DD-12) |
| Leasing one DUT out of several on an exporter | Members bind whole exporters (DD-15) |

*Future Possibilities* describes work that builds on this JEP.

### Ports

A port is a named connection point on a driver. Each forward connects a
`provides` port to a `requires` port.

| Direction | Meaning | Example |
| --- | --- | --- |
| `provides` | A service is listening; something may be forwarded *from* it | `rootcanal` on a Cuttlefish exporter — HCI on `127.0.0.1:7300` |
| `requires` | The driver will dial a local address; a forward may be delivered *to* it | `controller` on a `bt-peer` exporter — where its bumble stack expects an HCI controller |

A `TcpNetwork` child can expose a provided port:

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

For a `requires` port, the exporter binds a local listener that the driver
dials:

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

The `bt-peer` driver still dials `127.0.0.1:7300`. The exporter forwards that
connection to the remote rootcanal; using the forward requires no changes to
the driver's Python code.

Every port also has an `attachment` describing how it connects. It defaults
to `socket`, the only value a forward accepts. Drivers may report `wired` or
`wireless` for physical connection points; these appear in the exporter
report, and a forward that names one is rejected (DD-14).

### Declaring a multi-exporter lease

```yaml
apiVersion: jumpstarter.dev/v1alpha1
kind: Lease
metadata:
  name: projection
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

The lease names members and ports. Exporters resolve addresses locally, and
the controller determines direction from reported ports at bind time (DD-6,
DD-13).

Each member uses the existing `selector` or `exporterRef` fields with a role
name. The scalar lease form remains supported (DD-2).

A member marked `optional: true` may be omitted at binding; its role then
resolves to `None` on the client. A forward that references an omitted
optional member is `Disabled`, with a status message naming that member. It
is not established and does not prevent the rest of the lease from becoming
`Ready`. If the optional member binds, every forward that references it is
required to connect normally.

#### CAN example

Two ECUs can share a CAN segment through `socketcand` or another TCP bridge:

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

The controller checks that both ports exist, their directions complement
each other, and any declared protocols agree.

### Acquiring and using a multi-exporter lease

The existing commands take members and forwards; there is no parallel
command set.

```console
$ jmp create lease \
    --member phone=device-type=phone \
    --member headunit=device-type=headunit \
    --forward bt=headunit.rootcanal:phone.controller \
    --duration 45m
projection

$ jmp get lease projection
NAME                 ENDED   CLIENT      EXPORTER                       AGE
projection   false   ci-runner   phone=rack3-phone-4,            12s
                                         headunit=virt-hu-7b2c
```

Users can discover port names from the exporter report (DD-7):

```console
$ jmp get exporter virt-hu-7b2c -o json | jq '.status.devices[].ports'
[{"name":"rootcanal","direction":"provides","protocol":"hci-h4"}]
```

In a shell, roles become top-level names alongside the usual driver clients:

```console
$ jmp shell --lease projection
jumpstarter ⚡ projection ➤ j phone adb shell getprop ro.product.model
phone-under-test
jumpstarter ⚡ projection ➤ j headunit power on
jumpstarter ⚡ projection ➤ j forward status
NAME   SRC                   DEST                MODE     STATE       A→B       B→A
bt     headunit.rootcanal    phone.controller    direct   connected   1.2 MiB   0.9 MiB
```

In Python, a multi-exporter lease is declared as a class. Each member is an
attribute annotated with the driver clients the test expects on that
exporter, and each forward is an attribute naming two `role.port`
endpoints:

```python
from datetime import timedelta

from jumpstarter.client.lease import LeaseMembers, forward, member
from jumpstarter.config.client import ClientConfigV1Alpha1
from jumpstarter_driver_adb.client import AdbClient
from jumpstarter_driver_bt_peer.client import BtPeerClient
from jumpstarter_driver_composite.client import CompositeClient
from jumpstarter_driver_power.client import PowerClient


class Phone(CompositeClient):
    adb: AdbClient
    bt_peer: BtPeerClient


class Headunit(CompositeClient):
    power: PowerClient


class Projection(LeaseMembers):
    phone: Phone = member(selector="device-type=phone")
    headunit: Headunit = member(selector="device-type=headunit")
    bt = forward("headunit.rootcanal", "phone.controller")


config = ClientConfigV1Alpha1.load("default")

with config.lease(Projection, duration=timedelta(minutes=45)) as lease:
    with lease.connect() as members:          # members: Projection
        members.headunit.power.on()           # PowerClient.on
        members.bt.wait_connected(timeout=30) # Forward handle
        members.phone.bt_peer.start_peer('{"name": "Bumble-Phone"}')
        members.phone.bt_peer.wait_connection(timeout=60)
```

A type checker sees `members.phone` as `Phone` and `members.bt` as `Forward`.
It reports an undeclared member, a driver the member's protocol does not
list, and a wrong call signature. The rules are:

- **Members.** `member()` takes exactly one of `selector=` or `exporter=`;
  its overloads reject both or neither. The role is the attribute name with
  `_` replaced by `-`, or an explicit `role=`. `optional=True` requires a
  `| None` annotation, and a `| None` annotation requires `optional=True`,
  so the checker forces tests to handle an omitted member.
- **Driver shape.** The annotation is a `CompositeClient` subclass whose
  annotated attributes are driver names typed as client classes. A nested
  `CompositeClient` subclass describes a composite driver, and `| None`
  marks a driver that may be absent; reading an absent optional driver
  returns `None`. Annotations do not change `CompositeClient`'s constructor
  or its child lookup, so the class is an ordinary client of the member's
  exporter. Each client class implements a driver interface, which JEP-0011
  names by proto package, so the whole shape can be written without Python.
  `connect()` checks each bound member against it using the client class
  the exporter reports, and raises `TypeError` naming the role and driver on
  a mismatch.
- **Forwards.** `forward(a, b)` names two endpoints in any order, and the
  controller resolves direction (DD-13). `forward(src=..., dest=...)` pins
  `src` to the `provides` port and `dest` to the `requires` port. Either
  form takes `mode="auto" | "router" | "direct"`. Class creation rejects an
  endpoint whose role is not declared on the class.
- **Dynamic access.** `LeaseMembers.by_role` is a read-only mapping from
  role to client, for generic tooling such as the Mobly export.

`config.lease` is overloaded on its first argument. A `LeaseMembers`
subclass `M` yields `Lease[M]`, whose `connect()` yields an `M`. A selector string, or the
existing `selector=` and `exporter_name=` keywords, keeps the scalar
behavior, and `connect()` yields a single driver client. The request form
determines the response shape (DD-2).

`JumpstarterTest` accepts a `members` class next to `selector`. Its existing
`client` fixture then yields an instance of that class instead of a single
driver client:

```python
class TestProjection(JumpstarterTest):
    members = Projection

    def test_pairs(self, client: Projection) -> None:
        client.headunit.power.on()
```

### Running existing multi-device suites

A lease with ADB-capable members can be exported as a Mobly testbed:

```console
$ jmp get lease projection -o mobly > testbed.yml
$ mobly_test.py -c testbed.yml --test_bed projection
```

The exported `AndroidDevice` controllers use locally forwarded ADB endpoints
and retain the member names as device labels. Existing tests and results
pipelines can use these endpoints. The lifetime of the local ADB forwards
remains an open question.

### How a forward comes up

The exporter reuses `TemporaryTcpListener` and `forward_stream()` from
`TcpPortforwardAdapter`, replacing the client stream with a peer stream:

1. After binding, the controller validates the ports and sends setup
   instructions over each exporter's existing `Listen` stream.
2. Each exporter calls `DialPeer` for connection details and credentials.
3. For a direct-eligible pair, the `requires` side first establishes mTLS
   with a bounded timeout. Both peers validate their controller-issued,
   per-forward certificate identities; only then does the requiring side send
   `peer_token` inside the encrypted channel. Otherwise, or if that attempt
   fails in `Auto` mode, both endpoints call `RouterService.Stream` with
   tokens sharing one unique per-forward subject (DD-4, DD-5).
4. The `provides` side dials its local service. The `requires` side listens
   on its configured address. Both splice each accepted local connection to
   that connection's peer stream using `forward_stream()`.

```{mermaid}
flowchart TD
    lease["Lease: projection"]
    phone["Exporter: rack3-phone-4<br/>bt_peer · requires: controller<br/>Listens on 127.0.0.1:7300"]
    headunit["Exporter: virt-hu-7b2c<br/>cuttlefish · provides: rootcanal<br/>Dials 127.0.0.1:7300"]
    router["RouterService"]

    lease -.->|"status.members: phone"| phone
    lease -.->|"status.members: headunit"| headunit
    phone <-->|"Direct peer: preferred in same zone"| headunit
    phone <-->|"Router fallback"| router
    router <--> headunit
```

The client is not in the data path.

### Attaching media, simulated and physical

Drivers expose the connection points that forwards carry:

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

Control drivers configure and observe a medium separately from its forwarded
traffic. For example, `jumpstarter-driver-netsim` uses the REST API to list
devices, toggle radios, reset state, and collect pcap captures. It is not a
forward endpoint.

HCI connects a host to a controller. Joining two controllers requires a
link-layer port instead; joining two HCI `provides` ports is invalid (DD-6,
DD-9). Some interfaces also need protocol handling at the endpoint: netsim's
`PacketStreamer` requires a gRPC call carrying `ChipInfo`, so its consumer
must implement that handshake.

Leases of virtual radios forward simulator traffic. Physical radio peers
communicate over the air and need only the shared lease. Physical wired
media reach a forward only through a socket bridge, such as `socketcand`.
Switching the physical wiring itself is out of scope (DD-14).

### Projection example: virtual and physical

**Virtual.** A Cuttlefish phone exporter runs a vendor phone image with the
projection app and its developer-mode server. A `projection-rx` driver runs
a receiver process in the head unit exporter, since AOSP automotive images
do not include that receiver. The phone provides the port and the receiver
dials it:

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

`projection-wifi` reaches the server through the guest's Wi-Fi interface;
`projection` reaches it over ADB. The test chooses which path to exercise
(DD-13). Neither forward alone tests the Bluetooth-to-Wi-Fi handover (DD-10).

**Physical.** A real phone and head unit pair over the air and project over
the head unit's own Wi-Fi access point. The lease needs no forwards; it
gives the test both devices for the same lifetime. Placement comes
from exporter labels:

```yaml
spec:
  members:
    - name: phone
      selector: { matchLabels: { device-type: phone, rf-domain: rack-3 } }
    - name: headunit
      selector: { matchLabels: { device-type: aaos-headunit, rf-domain: rack-3 } }
```

Naming the rack keeps the devices within range but limits the pool to that
rack. A socket bridge can still carry a wired segment, such as CAN, to the
head unit.

### API / Protocol Changes

The API adds fields to existing types and one RPC. Existing fields retain
their meanings.

**Driver report** — ports are optional, and an exporter that reports none
simply cannot participate in forwards (DD-7):

```protobuf
message DriverInstanceReport {
  // ... fields 1-5 unchanged; 6 and 7 are reserved by JEP-0011 and the
  // native-gRPC proposal (file_descriptor_proto, native_services) ...
  repeated PortReport ports = 8;   // NEW, optional
}

message PortReport {
  string name = 1;                 // "rootcanal", "controller"
  PortDirection direction = 2;     // PROVIDES | REQUIRES
  optional string protocol = 3;    // free-form; compared only if both ends set it
  PortAttachment attachment = 4;   // UNSPECIFIED is treated as SOCKET (DD-14)
}

enum PortDirection {
  PORT_DIRECTION_UNSPECIFIED = 0;
  PORT_DIRECTION_PROVIDES = 1;
  PORT_DIRECTION_REQUIRES = 2;
}

enum PortAttachment {
  PORT_ATTACHMENT_UNSPECIFIED = 0; // socket
  PORT_ATTACHMENT_SOCKET = 1;      // forwardable byte stream
  PORT_ATTACHMENT_WIRED = 2;       // physical cable or bus; not forwardable
  PORT_ATTACHMENT_WIRELESS = 3;    // over-the-air medium; not forwardable
}
```

Port names are unique across all `DriverInstanceReport` entries from one
exporter, not merely within one driver instance. The exporter validates this
before registration, and the controller rejects a registration containing a
duplicate with `INVALID_ARGUMENT`. A `(member, port)` therefore resolves to
exactly one driver UUID; the resolved UUID is included in setup instructions
so the exporter never selects a local endpoint by name alone. The `listen`
address of a `requires` port is deliberately **absent**: it is local to the
exporter and no other component needs it (DD-6).

**`LeaseSpec`** gains two optional lists:

```go
// Member and forward names must each be unique before binding or token creation.
// +kubebuilder:validation:XValidation:rule="self.members.all(m, self.members.filter(x, x.name == m.name).size() == 1)",message="member names must be unique"
// +kubebuilder:validation:XValidation:rule="self.forwards.all(f, self.forwards.filter(x, x.name == f.name).size() == 1)",message="forward names must be unique"
type LeaseSpec struct {
    // ... all existing fields unchanged ...

    // Members of a member-form lease. When empty, the lease binds a single
    // exporter using the top-level Selector/ExporterRef exactly as before.
    // +kubebuilder:validation:MaxItems=8
    Members []LeaseMember `json:"members,omitempty"`

    // Port forwards between members. Requires Members.
    Forwards []LeaseForward `json:"forwards,omitempty"`
}

// Exactly one non-empty selection source is required for every member.
// +kubebuilder:validation:XValidation:rule="((((has(self.selector.matchLabels) && size(self.selector.matchLabels) > 0) || (has(self.selector.matchExpressions) && size(self.selector.matchExpressions) > 0)) ? 1 : 0) + ((has(self.exporterRef) && has(self.exporterRef.name) && size(self.exporterRef.name) > 0) ? 1 : 0)) == 1",message="exactly one of selector or exporterRef.name is required"
// +kubebuilder:validation:XValidation:rule="self.name != 'forward' && self.name != 'forwards'",message="member name is reserved"
type LeaseMember struct {
    // DNS-label syntax keeps names usable in the CLI and generated formats.
    // Python maps role names to LeaseMembers attributes, "-" becoming "_".
    // +kubebuilder:validation:MaxLength=63
    // +kubebuilder:validation:Pattern=`^[a-z0-9]([-a-z0-9]*[a-z0-9])?$`
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
    Src  *ForwardEndpoint `json:"src,omitempty"`   // must resolve to `provides`
    Dest *ForwardEndpoint `json:"dest,omitempty"`  // must resolve to `requires`

    // Auto (default) | Router | Direct.
    // Auto prefers a direct peer connection when both members are in the
    // same network zone and falls back to the router; Direct fails rather
    // than falling back; Router never attempts a direct dial (DD-4).
    Mode string `json:"mode,omitempty"`
}

type ForwardEndpoint struct {
    Member string `json:"member"`
    Port   string `json:"port"` // exporter-wide unique port name
}
```

**`LeaseStatus`** gains parallel lists and keeps its scalar:

```go
type LeaseStatus struct {
    // ... all existing fields unchanged ...
    // ExporterRef stays authoritative for scalar leases and is left nil for
    // every lease requested through Members, including one-member lists.

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
    State   string `json:"state"` // Pending|Disabled|Connecting|Connected|Reconnecting|Failed
    Mode    string `json:"mode,omitempty"`
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

`ExporterSpec` gains one optional, admin-owned field, and `LeaseStatus`
records the groups a lease holds (DD-16):

```go
    // ExporterSpec: exporters that share a non-empty value serve the same DUT.
    // At most one lease holds the group at a time. Set by an administrator,
    // never by the exporter's own registration.
    // +kubebuilder:validation:MaxLength=63
    ExclusionGroup string `json:"exclusionGroup,omitempty"`

    // LeaseStatus: groups held by this lease, recorded at bind time.
    ExclusionGroups []string `json:"exclusionGroups,omitempty"`
```

The existing CEL rules are extended, not replaced. The current top-level
"one of selector or exporterRef is required" rule gains a `members` arm and
mutual exclusion. Per-member CEL requires exactly one *non-empty* `selector`
or `exporterRef.name`; both set and both unset are rejected. Additional rules
enforce unique and immutable member names, unique forward names, forwards
referencing declared members, member immutability (mirroring `tags` and
`context`), and exactly one of `between` or `src`+`dest` per forward. Duplicate
forward names are thus rejected before the controller derives stream subjects
or creates status maps.

**Protocol** — additions to existing messages and one new RPC. These are the
wire definitions; the similarly named Go structs above describe the CRD only:

```protobuf
enum LeaseForwardMode {
  LEASE_FORWARD_MODE_UNSPECIFIED = 0; // Auto
  LEASE_FORWARD_MODE_AUTO = 1;
  LEASE_FORWARD_MODE_ROUTER = 2;
  LEASE_FORWARD_MODE_DIRECT = 3;
  reserved 4;
}

enum LeaseForwardState {
  LEASE_FORWARD_STATE_UNSPECIFIED = 0;
  LEASE_FORWARD_STATE_PENDING = 1;
  LEASE_FORWARD_STATE_DISABLED = 2;
  LEASE_FORWARD_STATE_CONNECTING = 3;
  LEASE_FORWARD_STATE_CONNECTED = 4;
  LEASE_FORWARD_STATE_RECONNECTING = 5;
  LEASE_FORWARD_STATE_FAILED = 6;
}

enum ForwardSide {
  FORWARD_SIDE_UNSPECIFIED = 0;
  FORWARD_SIDE_PROVIDES = 1;
  FORWARD_SIDE_REQUIRES = 2;
}

message LeaseMember {
  string name = 1;
  oneof selection {
    LabelSelector selector = 2;
    string exporter_name = 3;
  }
  bool optional = 4;
  bool allow_disabled = 5;
}

message ForwardEndpoint {
  string member_name = 1;
  string port_name = 2; // Unique within the member exporter.
}

message LeaseForwardBetween {
  repeated ForwardEndpoint endpoints = 1; // Exactly two.
}

message LeaseForwardDirected {
  ForwardEndpoint src = 1;  // Must resolve to PROVIDES.
  ForwardEndpoint dest = 2; // Must resolve to REQUIRES.
}

message LeaseForward {
  string name = 1;
  oneof topology {
    LeaseForwardBetween between = 2;
    LeaseForwardDirected directed = 3;
  }
  LeaseForwardMode mode = 4;
}

message LeaseMemberStatus {
  string name = 1;
  optional string exporter_uuid = 2; // Absent when an optional member is omitted.
  int32 priority = 3;
  bool spot_access = 4;
}

message LeaseForwardStatus {
  string name = 1;
  LeaseForwardState state = 2;
  LeaseForwardMode mode = 3; // Transport actually in use when connected.
  optional string message = 4;
}

message RequestLeaseRequest {
  google.protobuf.Duration duration = 1; // unchanged
  LabelSelector selector = 2;            // unchanged; scalar form only
  repeated LeaseMember members = 3;      // NEW
  repeated LeaseForward forwards = 4;    // NEW
}

message GetLeaseResponse {
  // ... fields 1-6 unchanged; exporter_uuid set only for scalar leases ...
  repeated LeaseMemberStatus members = 7;   // NEW
  repeated LeaseForwardStatus forwards = 8; // NEW
}

message DialRequest {
  string lease_name = 1;             // unchanged
  optional string member_name = 2;   // NEW: required for member-form leases
}

// Listen is a server stream from the controller to an authenticated exporter.
// Fields 1 and 2 retain the existing client-connection instruction. Exactly
// one instruction is populated. Forward setup is idempotent by
// (lease_uid, forward_name, member_name).
message ListenResponse {
  string router_endpoint = 1;                // unchanged
  string router_token = 2;                   // unchanged
  optional ForwardSetup forward_setup = 3;   // NEW
  optional ForwardTeardown forward_teardown = 4; // NEW
}

message ForwardSetup {
  string lease_name = 1;
  string lease_uid = 2;
  string forward_name = 3;
  string member_name = 4;
  string peer_member_name = 5;
  ForwardSide side = 6;
  string local_driver_uuid = 7; // Resolved from the exporter-wide unique port.
  string local_port_name = 8;
  string peer_port_name = 9;
  LeaseForwardMode mode = 10;
}

message ForwardTeardown {
  string lease_uid = 1;
  string forward_name = 2;
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

message DirectPeerParameters {
  string endpoint = 1; // Dial target for REQUIRES; empty for PROVIDES.
  bytes ca_certificate = 2; // Trust root for the opposite endpoint.
  bytes certificate = 3;    // This endpoint's short-lived certificate.
  bytes private_key = 4;    // This endpoint's short-lived private key.
  string expected_peer_identity = 5;
}

message DialPeerResponse {
  string router_endpoint = 1;
  string router_token = 2;
  optional DirectPeerParameters direct = 3;
  optional string peer_token = 4;
  bool prefer_direct = 5;
}
```

Forward credentials are not carried on `Listen`. After receiving
`ForwardSetup`, each exporter calls authenticated `DialPeer`; the controller
returns side-specific, short-lived router and (when eligible) per-forward mTLS
credentials. The provider uses them for its configured peer listener, and the
requiring side uses them to dial and verify that listener. Private keys remain
inside the authenticated controller channel.

For an ordinary client connection, fields 1 and 2 are both populated and
fields 3 and 4 are absent. For setup or teardown, only the corresponding
optional message is populated. An old exporter reports no ports, so it cannot
be selected for a forward and never receives fields 3 or 4 of
`ListenResponse`. A new exporter checks those fields before treating fields 1
and 2 as a client connection. Unknown fields remain safe under proto3.
`ReleaseLeaseRequest` and `ListLeasesRequest` are untouched. `RouterService.Stream` and its protobuf remain unchanged, but its
token validation changes as described in DD-5.

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

- **Hardware:** virtual devices need KVM-capable hosts but no physical
  radios. Physical devices use existing harnesses. Leases that mix a
  virtual device with a physical radio are not supported (DD-11).
- **RF range and isolation:** physical radio peers must be within range.
  Shared labs may need shielded enclosures or channel planning. Exporter
  labels such as `rf-domain: rack-3` pin placement through selectors; the
  controller does not measure RF interference.
- **Bluetooth latency:** HCI flow control and audio buffering can be more
  sensitive than supervision timeouts. Measure both router and direct paths
  against the intended workloads (DD-4).
- **Wi-Fi simulation:** vhost-user needs a frame bridge, and TCP adds
  head-of-line blocking that may affect medium timing (DD-10).
- **Projection receiver:** virtual head units use a software receiver, such as
  Google's Desktop Head Unit (see *Projection example*).
- **Listener isolation:** fixed `requires` addresses rely on each exporter
  owning its network namespace. Multiple host-networked exporters sharing
  one machine are unsupported.
- **Member loss:** report `Ready=False` with the failed role and retain
  surviving members for diagnostics (DD-3).

## Design Decisions

### DD-1: Multi-exporter representation — extend `Lease` vs. a new CR

**Alternatives considered:**

1. **Extend `Lease`** with `spec.members[]` / `status.members[]`; a
   single-exporter lease uses the same selection logic.
2. **A new `LeaseGroup` CR owning N child `Lease` CRs.**
3. **Client-side coordination only** — the client acquires N leases and
   correlates them by tag.

**Decision:** Option 1 — extend `Lease`.

**Rationale:** An exporter claim is stored on the lease. The current controller writes
`lease.Status.ExporterRef` and checks other active leases through
`ListActiveLeases` → `attachExistingLeases` → `filterOutLeasedExporters`.
Writing all member claims in one `Status().Update` prevents a lease from
persisting a partial acquisition.

Child leases would bind independently and require acquisition timeouts,
release-and-retry behavior, and contention handling. Client-side coordination
has the same partial-acquisition problem. Both approaches also need a way to
select a member when dialing.

This atomic write does **not** guarantee exclusivity across leases. Two
reconcilers can read stale claims and write conflicting selections to
different lease objects. That race already exists; leases with more members create
more opportunities to encounter it. This JEP does not change it.

### DD-2: Keep `status.exporterRef` scalar; add `status.members[]` alongside

**Alternatives considered:**

1. **Keep the scalar, add a parallel list.** `status.exporterRef` stays
   authoritative for scalar-form leases and is left **nil** for every
   member-form lease, which populates `status.members[]` instead.
2. **Promote the scalar to a list** and migrate every reader.
3. **Always populate both**, setting `status.exporterRef` to the first member.

**Decision:** Option 1.

**Rationale:** Existing consumers retain the scalar field for leases requested
through the top-level selector or exporter reference. For a lease requested
through `members`, even when the list contains exactly one entry, an old
reader sees nil and treats the lease as unbound rather than selecting an
arbitrary role. Populating the scalar with the first member would misroute
consumers such as the JEP-0016 Host Orchestrator façade. Replacing it with a
list would require every consumer to migrate.

`GetLeaseResponse.exporter_uuid` follows the same convention. A scalar-form
lease returns a bare driver client. An explicit one-member or multi-member
lease returns a `LeaseMembers` instance with one attribute per role. The request
form, rather than the number of bound exporters, therefore determines a
stable response shape.

`DialRequest.member_name` selects a role. Omitting it for any member-form
lease returns `INVALID_ARGUMENT` listing the available roles.

### DD-3: Behavior when a member is lost mid-lease

**Alternatives considered:**

1. **Fail the lease** — set `Ready=False`, name the failed role, keep the
   surviving members held until release or expiry.
2. **End the whole lease immediately** on any member loss.
3. **Continue silently** with the surviving members.

**Decision:** Option 1.

**Rationale:** Keeping surviving members leased lets the client collect logs and
artifacts before release. The lease reports the failed role with
`Ready=False`, and the client raises on the next call into that role.
Immediate release would remove that diagnostic access; silently continuing
could hide an incomplete test. This behavior applies after binding;
acquisition remains all-or-nothing.

### DD-4: Forward transport — router peer streams vs. client relay vs. pure P2P

**Alternatives considered:**

1. **Router peer streams with an optional direct P2P fast path.**
2. **Client-relayed** — the client pumps bytes between two streams.
3. **Direct peer-to-peer only.**

**Decision:** Option 1.

**Rationale:** The router connects exporters that cannot reach each other, including
edge devices behind NAT. Direct connections avoid router load and reduce
latency where peers are reachable. Client relay adds a dependency on the
client's network and lifetime and is not offered as a mode.

In `Auto` mode, same-zone pairs attempt a direct connection first, then fall
back to the router after a bounded timeout. `Router` forces the router path
for testing, and `Direct` fails if a direct connection cannot be established.
Both paths authenticate against the lease.

Direct connections also avoid ingress-related stream failures where the
router route passes through an ingress. In the single-node prototype, a
router forward had a median round-trip time of 0.70 ms versus 0.05 ms direct;
rootcanal HCI commands took about 45 ms either way (DD-9). These measurements
do not establish cross-node or sustained-throughput limits. Phase 4's Wi-Fi
frame bridge requires separate latency testing.

### DD-5: Keep the router protobuf; bind pairing to forward claims

**Alternatives considered:**

1. **Reuse the `RouterService.Stream` RPC and forwarding path**, while
   strengthening its JWT validation for peer-pair claims.
2. **Add a peer-specific RPC** to `router.proto` with explicit A/B roles.
3. **Reuse the current shared exporter subject unchanged**, relying only on
   controller-side token issuance.

**Decision:** Option 1 — no `router.proto` change, with authorization changes
inside `RouterService.Stream`.

**Rationale:** The current router uses the JWT `sub` as its pending-stream key;
a shared subject such as `jumpstarter exporter` would allow unrelated
forwards to collide. For each forward, the controller instead sets `sub` to
the stable UUIDv5 derived from `(lease UID, forward name)`. Each signed token
also carries `lease_uid`, `forward_name`, `source_exporter`, `target_exporter`,
`source_member`, `target_member`, and `side` (`provides` or `requires`).

The router parses these claims, keys pending streams by the unique subject,
and pairs only two tokens whose exporter/member fields are reciprocal and
whose sides are complementary. A duplicate token from the same side is
rejected rather than paired. `DialPeer` has already authenticated the caller
as the bound source exporter before issuing its token, and token expiry is
bounded by the lease. This preserves the existing byte-forwarding RPC while
preventing cross-forward and same-side pairing. A second RPC would duplicate
the stream path without improving these checks.

### DD-6: Named ports with direction, not addresses

**Alternatives considered:**

1. **Named ports on both ends**, each declaring `provides` or `requires`;
   the lease references names only.
2. **Raw addresses in the lease** — `from: headunit.rootcanal`,
   `to: {member: phone, listen: 127.0.0.1:7300}`.
3. **Untyped named endpoints** — names on both ends but no direction.

**Decision:** Option 1.

**Rationale:** Named ports let exporter configuration own addresses. A lease can
reference `headunit.rootcanal` and `phone.controller` without knowing their
loopback addresses or port numbers.

Direction allows the controller to reject invalid connections without
understanding the device protocol. For example, a Bumble host can attach to
a rootcanal HCI controller; connecting two rootcanal HCI services cannot
provide that relationship. The controller rejects the latter because both
ports declare `provides`.

### DD-7: Ports are an optional part of the exporter report

**Alternatives considered:**

1. **A new optional repeated `ports` field** on `DriverInstanceReport`,
   mirrored into `ExporterStatus.Devices[]`.
2. **Encode ports as driver-instance labels**, which already flow through to
   `ExporterStatus.Devices[].Labels` — zero proto and CRD change.
3. **No reporting** — ports live only in exporter config, and forwards fail
   at connect time if misconfigured.

**Decision:** Option 1. Exporters without reported ports can join leases
but cannot participate in forwards.

**Rationale:** Reported ports support discovery through `jmp get exporter` and
validation before a forward starts. Configuration alone would defer errors
to connection time and leave clients unable to discover available names.

Labels would overload driver metadata: a provided port can correspond to a
`TcpNetwork` child, while a required port describes a connection the driver
needs. A structured field represents both without synthetic driver entries
or separate label conventions.

An absent `ports` field defaults to an empty list, so old exporters continue
to register and serve ordinary leases. Reported names are exporter-wide
unique; duplicate names across driver instances reject registration, making a
lease endpoint unambiguous. The local `listen` address stays in exporter
configuration and is not reported.

### DD-8: No protocol taxonomy; direction plus an optional tag

**Alternatives considered:**

1. **A `medium` enum** (`bluetooth | wifi | uwb | serial | can`) matched
   between endpoints.
2. **A `format`/wire-protocol token** matched for equality.
3. **Direction only**, with an optional free-form `protocol` compared solely
   when both ends declare it.

**Decision:** Option 3.

**Rationale:** A medium name does not establish wire compatibility: rootcanal HCI
and netsim `PacketStreamer` both carry Bluetooth traffic but use different
protocols. A wire-format token alone also misses direction errors such as
connecting two HCI controllers.

Direction is required. A free-form `protocol` adds an optional compatibility
check when both ends declare it, without a centrally maintained taxonomy.
If either end omits the tag, the forward may connect despite incompatible
protocols and fail when data is exchanged. Forwarding guarantees byte
transport, not protocol compatibility.

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

**Decision:** Use option 2 for Phase 1, with option 1 as a fallback.
Option 3 is the proposed extension beyond Cuttlefish; its use as a Cuttlefish
controller remains unverified.

**Rationale:** Cuttlefish reaches rootcanal through TCP ports derived from
`rootcanal_instance_num`: HCI `7300+N`, link `7400+N`, test `7500+N`, and
BLE link `7600+N`. These provide two attachment choices:

- Sharing one controller requires the forward before the second CVD boots.
  That controller becomes a shared failure point.
- Federating controllers lets each CVD keep its own radio controller. It
  requires standalone rootcanal (`--netsim_bt=false`), two forwarded link
  ports, and an `add_remote` command on the private test channel after the
  forwards are established. Netsim does not expose these link ports.

Bumble's virtual `Controller` and `RemoteLink` offer a programmable shared
medium. This requires a controller driver; the existing `bt-peer` creates a
host `Device` and can use a forwarded HCI transport without Python changes.
Sustained A2DP performance with a Python controller still needs testing.

A dedicated bridge-driver interface is unnecessary for these socket
connections. Guest-side shims would change the stack under test. Protocol
setup remains the responsibility of endpoint drivers.

**Prototype results (2026-09-01–02).** Manual tests used two Cuttlefish Pods
on one kind node with stand-in TCP relays, exercising shared and federated
rootcanal configurations. Tests covered discovery, SSP pairing, HFP, A2DP,
AVRCP, audio streaming, and reconnection after toggling Bluetooth. A later
test connected a CVD to a Bumble `bt-peer` over Jumpstarter's router. Adding
an HFP Audio Gateway to the peer also delivered a simulated incoming call
to the head unit. These tests exercised Bumble as a host, not as a controller.

The measured router-forward round-trip was 0.70 ms median (p90 0.91 ms,
n=100), versus 0.05 ms direct to the same endpoint. Rootcanal HCI commands
took about 45 ms on either path. Multi-node tests remain required.

The prototypes identified these implementation requirements:

- **Controller recovery:** connecting to rootcanal's test port and closing
  before its banner is written can abort rootcanal. Its process restart did
  not restore the guest connector; recovery required `cvd restart`. Keep
  the test channel private, derive forward health from the stream rather
  than connect-and-close probes, and report controller loss as `Failed` and
  lease `Degraded`.
- **Unique Bluetooth addresses:** rootcanal assigns and reuses addresses
  such as `da:4c:10:de:00:<n>`. Federated controllers and attaching peers can
  collide. Assign member addresses before host power-on and pairing.
- **Guest addressing:** Cuttlefish guests use identical network address
  plans. L2/L3 bridges require NAT or re-addressing; L4 forwards do not.
- **Reconnection:** an ingress reload followed by a 240-second worker drain
  cut the prototype's controller and HCI streams. The exporter reconnected
  its controller stream but the stand-in forward stayed down. Forward
  endpoints must reconnect, update readiness, and notify drivers that need
  to restore protocol state.
- **Exporter identity and versions:** duplicate exporter processes split
  sessions and caused driver-UUID `KeyError` failures. A separately built
  forward endpoint with a mismatched network-driver version returned EOF.
  Run one process per identity and keep forwarding in the exporter runtime.
- **Image and bond state:** the tested GSI needed classic Bluetooth profile
  properties enabled. Bumble bonds need a keystore to survive peer restarts;
  otherwise the DUT must forget the old bond. Release leases through their
  lifecycle API; deleting a lease during acquisition left the prototype
  client retrying `not found`.

### DD-10: Wi-Fi and projection — what a forward carries

**Alternatives considered:**

1. **Forward the `mac80211_hwsim` frame socket** between members so both
   share one simulated medium.
2. **Attach at netsim's 802.11 MAC chip** — Wi-Fi as another chip kind on the
   `PacketStreamer` port.
3. **Forward the projection session at L4** — carry the projection's own TCP
   connection, over the guest's real Wi-Fi NIC, and simulate no radio.

**Decision:** Deliver option 3 in Phase 2. Phase 4 adds simulated Wi-Fi and
the Bluetooth-to-Wi-Fi handover, using netsim where supported or a frame
bridge for `mac80211_hwsim`/`wmediumd`.

**Rationale:** An L4 forward exercises projection version negotiation, TLS,
service discovery, video, audio, and input. It does not exercise Bluetooth
credential exchange, Wi-Fi Direct association, RSSI, roaming, or channel
loss. This provides a useful projection test before medium simulation is
available.

Cuttlefish's `virtio_mac80211_hwsim` connects to `wmediumd` through vhost-user,
which uses shared memory and file-descriptor passing. That connection cannot
be forwarded as a byte stream across hosts. Option 1 needs a bridge on each
exporter to terminate the local interface and exchange 802.11 frames, with
datagram support considered separately. Option 2 can reuse netsim's packet
transport where its Wi-Fi support is sufficient.

For L4 forwarding, a route and forwarding rule expose the guest's Wi-Fi
address through its OpenWrt AP. The driver can therefore provide the same
projection server through ADB (`projection`) or the guest's Wi-Fi address
(`projection-wifi`). The test selects the path (DD-13).

**Prototype results (2026-09-01).** A vendor phone image in one Cuttlefish Pod
projected to a desktop receiver in another Pod through a stand-in relay,
first over ADB and then over the guest's Wi-Fi address. The session completed
version negotiation, TLS, service discovery, and the phone's first-run flow,
and displayed the launcher with maps, media, and telephony. A mostly static
screen transferred about 0.6 MB of video in three minutes; this does not
establish a sustained-throughput limit.

The receiver required a display and open stdin. An aborted session required
a phone-side server restart. The phone joined Wi-Fi only after its validated
Ethernet connection was removed. These requirements belong in the reference
drivers.

### DD-11: A lease's devices are all virtual or all physical

**Alternatives considered:**

1. **Homogeneous leases:** two virtual devices or two physical devices.
2. **Mixed leases**, via a gateway exporter owning a real radio adapter,
   presented as a `provides` port that the virtual side attaches to as it
   would to any other controller.

**Decision:** Option 1.

**Rationale:** Joining physical and simulated radios requires a nearby
hardware adapter and a way to allocate that shared RF resource. Bluetooth
may use a USB HCI adapter; Wi-Fi needs suitable radio hardware. Option 2
adds hardware integration to the lease and forwarding work. A software
model of a physical peer is useful but does not test the physical device's
stack.

### DD-12: Access policy and port validation timing

**Alternatives considered:**

1. **Per-member policy evaluation, bind-time port validation.** Each member
   is evaluated against `ExporterAccessPolicy` exactly as a standalone lease
   would be; forwards are validated against the bound exporters' reports.
2. **Selection-time port validation** — ports surfaced as exporter CR labels
   so member selectors only match exporters that have the required ports.
3. **A multi-exporter policy CRD** with rules over lease shape, size, and count.

**Decision:** Option 1.

**Rationale:** Each member must satisfy the same access policy as an independent
lease request. Lease priority is the minimum member priority, and duration
is bounded by the minimum per-member `maximumDuration`.
`status.members[].priority` preserves the individual values for inspection.

The existing selector pipeline matches exporter CR metadata labels, while
ports are reported in `ExporterStatus.Devices[]`. The controller therefore
validates ports after selecting and binding exporters. An invalid forward
leaves the members held for inspection until release or expiry.

Option 2 needs reported ports to be selectable, which the selector pipeline
does not support. Option 3 adds a policy CRD before there is operational
experience with multi-member leases.

### DD-13: Infer direction, never infer topology

**Alternatives considered:**

1. **Infer direction only.** A forward names its two endpoints in any order
   (`between`); the controller decides which is `provides` and which is
   `requires` from the reported ports. Which ports are joined stays explicit.
2. **Infer topology too** — auto-forward every `provides`/`requires` pair the
   bound exporters happen to expose, with no `forwards` stanza at all.
3. **Infer nothing** — the author states `src` and `dest` on every forward.

**Decision:** Option 1, with option 3 retained as an explicit form.

**Rationale:** Port direction is already in the exporter report. The
controller can resolve it without requiring the lease author to repeat it.
The test must still choose which ports to connect: forwarding `projection`
and forwarding `projection-wifi` exercise different paths.

Automatic topology would make connections depend on the ports exposed by
whichever exporters were selected. Multiple possible matches would be
ambiguous and could create data paths the test did not request.

Explicit `src`/`dest` remains available when a test requires a particular
direction; the controller validates it against the report. CLI shorthand may
expand into explicit `spec.forwards[]` entries before submission so the
stored topology remains inspectable.

### DD-14: Forwards carry socket ports only

**Alternatives considered:**

1. **Report the attachment; forward sockets only.** Add an `attachment`
   field (`socket | wired | wireless`) to `PortReport`. Forwards accept only
   `socket` ports; other values are reported but rejected.
2. **Route physical media.** Add a controller-driven exporter class that
   switches relays, VLANs, or signal gateways between members' ports.
3. **Omit physical ports.** Describe only socket ports and leave physical
   connection points undeclared.

**Decision:** Option 1.

**Rationale:** Physical routing needs mechanisms that byte forwarding does
not: devices that serve several exporters at once, allocation of their
channels across leases, fail-closed isolation at release, electrical
compatibility checks, and placement constraints during binding. Shared
power supplies and other lab infrastructure need the same mechanisms. They
are outside this JEP.

A physical CAN port and a `socketcand` bridge to it are different
connection points. Joining the physical port with a byte forward would
silently give up bus timing, so the controller rejects it. With option 3,
the controller could not tell the two apart, and exporters could not report
their physical ports.

The attachment describes how a port connects, not whether the device is
simulated: a physical ECU behind `socketcand` has a `socket` port and can be
forwarded to a virtual ECU.

### DD-15: Members bind whole exporters

**Alternatives considered:**

1. **Bind exporters.** A member binds one exporter and every DUT behind it.
2. **Bind individual DUTs.** Add an inventory of the DUTs behind each
   exporter and let a member bind one of them.

**Decision:** Option 1.

**Rationale:** Multi-DUT setups have several devices behind one exporter,
each with its own firmware, software, and configuration. Binding one of
them requires device identity, a model of which devices share a harness,
and per-device claims. None of these are needed to bind whole exporters.

The lease format has these properties:

- **Member selection is a `oneof`.** The CEL rule requires exactly one
  selection source, so each member has one unambiguous source.
- **Member status is additive.** `status.members[]` records the bound
  exporter, and clients ignore status fields they do not recognize.
- **Stored references use names, not UUIDs.** Driver instance UUIDs are
  generated each time the exporter starts. They appear only in runtime setup
  instructions. Lease specs and statuses refer to exporters, drivers, and
  ports by name, which stays stable across restarts.
- **Ports belong to driver instances.** A DUT is a subtree of the exporter's
  drivers, and each port names exactly one driver instance.
- **"Device" keeps its current meaning.** `ExporterStatus.Devices[]` lists
  reported driver instances. This JEP adds ports to those reports and uses
  *DUT*, not *device*, for the hardware under test.

Software state on an exporter can be selected through dynamic labels
(JEP-0017).

### DD-16: A DUT served by several exporters is claimed as one

**Alternatives considered:**

1. **Admin-assigned exclusion group.** Exporters that share a
   `spec.exclusionGroup` serve the same DUT. At most one lease holds the
   group; that lease may bind any subset of its exporters.
2. **Always lease the whole group together.** Binding one exporter in a
   group binds all of them into the lease.
3. **A DUT inventory.** Add a resource that lists the exporters attached to
   each DUT.
4. **Exporter-reported group label.** Each exporter declares its group in
   its registration labels.

**Decision:** Option 1.

**Rationale:** Two exporters attached to one DUT can already be leased by
two clients. One client might power-cycle the DUT while another is
flashing it through the second exporter. Multi-exporter leases make this
more common: labs split a DUT across exporters so that tests can combine
them. Option 3 needs the device identity and claims that DD-15 leaves out; a
group needs one field.

The group constrains who may hold the DUT, not which exporters a test
uses. A test that needs only power and console binds that one exporter;
the others stay unavailable to other leases until release. Option 2 would
add unrequested exporters to the lease and its forwards, and a missing
exporter in the group would block every lease on the DUT.

The group is an admin-owned spec field, not a label. Exporters overwrite
their `jumpstarter.dev/` labels at every registration, so option 4 would
let a compromised exporter join another DUT's group and make that DUT
unavailable whenever the compromised exporter is leased.

Group checks reuse the existing claim data: bound exporters on active
leases, plus the groups each lease recorded at binding. The existing
cross-lease race applies to groups as it does to exporters (DD-1).

Exclusion groups are the opposite of shared infrastructure (DD-14). A group
is several exporters for one DUT and one lease. A shared infrastructure
exporter is one exporter serving many DUTs and leases.

### DD-17: Declare multi-exporter leases as typed classes

**Alternatives considered:**

1. **A `LeaseMembers` subclass.** Members are annotated class attributes
   created with `member()`, and forwards are attributes created with
   `forward()`. `config.lease(Members)` yields that class from `connect()`.
2. **Keyword dictionaries.** `config.lease(members={...}, forwards=[...])`
   and a `members["role"]` mapping.
3. **Dynamic attributes.** Roles installed on the connected object at
   connect time.

**Decision:** Option 1.

**Rationale:** Driver clients are already typed classes, such as
`PowerClient` and `AdbClient`, but a lease's `connect()` yields an untyped
tree whose children are found by attribute lookup at runtime. Option 2
keeps that: a misspelled role or driver fails only when the test reaches
that line. With a class, a type checker resolves `members.phone.adb` to
`AdbClient` and reports an unknown role, a missing driver, or a wrong call
before the lease is requested.

The class is also the lease request. Roles, selectors, and forwards come
from one declaration that tests share, and endpoint names are checked
against declared roles when the class is created. `connect()` checks that
each bound exporter provides the drivers the member's class declares, so a
misconfigured exporter fails with the role and driver named rather than as
an `AttributeError` later in the test.

Option 3 gives no static types and lets a role shadow client attributes.
Class attributes cannot collide that way: `by_role` is the only reserved
name. Roles that are not valid identifiers map from underscores, as in
`head_unit` for `head-unit`, or use `member(role=...)`.

Each member is typed as a `CompositeClient` subclass, the same base that
exporter client trees and hand-written composite clients such as
`QemuClient` already use. Its annotations give the type checker the
children that `CompositeClient.__getattr__` resolves at runtime. The same
class is what the per-exporter codegen pipeline emits: one subclass per
member, one annotation per driver. Hand-written and generated member types
therefore have one shape, and a test can move from one to the other
without changing call sites. A `Protocol` would describe the same shape
but could not be instantiated, so `connect()` would return the untyped
tree and the checker's view would diverge from the runtime object.

The class holds only data: roles, each member's selector or exporter name
and `optional` flag, driver names mapped to client classes, and forwards
with their endpoints and mode. Every item has a language-neutral form. The
first four and the forwards are exactly `RequestLeaseRequest.members` and
`forwards`. Driver shapes stay on the client and become JEP-0011 proto
packages, because each client class implements one driver interface.
Member classes add annotations only; methods belong on driver clients. The
limit lets a code generator produce the same typed lease in other languages
without changing this API (see *Future Possibilities*).

## Design Details

### Deployment assumptions

Each exporter must own its network namespace and run one process per
exporter identity.

A virtual exporter can run in its own Pod, as proposed in JEP-0016. A
physical exporter can run on a dedicated edge device or in an isolated
container. This allows `requires` ports to use fixed loopback addresses and
keeps host-scoped simulator operations, such as netsim reset, within one
exporter's lease.

Multiple host-networked exporters on one machine are unsupported: their
listeners can collide and simulator control operations can cross lease
boundaries. Host networking remains usable for a single-exporter host or
local development.

Duplicate processes under one identity can both register but hold different
driver sessions, causing routed calls to fail. Each member exporter runs one
replica; duplicate registration must be rejected.

Network-zone configuration determines direct eligibility. Same-zone peers
attempt direct connections; exporters without a suitable peer route use the
router (DD-4).

### Binding: one pass, one write

The existing `reconcileStatusExporterRef` generalizes to
`reconcileStatusMembers`, keeping its selection pipeline intact per member:

```{mermaid}
flowchart TD
    select["Select policy-approved exporters<br/>matching the member selector"]
    filter["Exclude offline exporters, active claims,<br/>exporters whose exclusion group another lease holds,<br/>earlier picks, and exporters still cleaning up"]
    candidate["Keep the best candidate in memory<br/>Write no claims yet"]
    more{"More members?"}
    complete{"Every required member<br/>has a candidate?"}
    pending["Set Pending / Unsatisfiable<br/>Name the failing role; write no claims"]
    requeue["Requeue"]
    bind["Set status.members to all candidates<br/>Set priority to the minimum member priority"]
    commit["Commit one atomic Status().Update()"]

    select --> filter --> candidate --> more
    more -->|"Yes: next member"| select
    more -->|No| complete
    complete -->|No| pending --> requeue
    complete -->|Yes| bind --> commit
```

Candidates remain in memory until every required member resolves. The
selection pass excludes exporters already assigned to another member, so
two roles with the same selector receive distinct exporters.

Exclusion groups extend the claim check (DD-16). A candidate is excluded if
another active lease holds its group, either by binding an exporter in it or
through the groups recorded in that lease's `status.exclusionGroups`. Several
members of one lease may bind exporters in the same group. The binding write
records every group touched by the lease's bound exporters, so later
membership edits cannot silently release a DUT mid-lease. Spot-access
takeover follows the existing rules and applies to the whole group.

The scalar path uses the same selection code with a synthetic member and
writes the result to `status.exporterRef` (DD-2).

The existing cross-lease race remains: reconcilers can read stale claims
and commit conflicting selections to different lease objects. A later
reconcile detects the conflict and rebinds (DD-1).

### Lease state

```{mermaid}
flowchart TD
    pending["Pending"]
    available{"All required members available?"}
    unsatisfiable["Unsatisfiable<br/>No exporters held"]
    bound["All member claims committed<br/>in one status write"]
    forwards["ForwardsUp"]
    ready["Ready"]
    degraded["Degraded"]
    ended["Ended"]

    pending --> available
    available -->|No| unsatisfiable
    unsatisfiable -->|Requeue| pending
    available -->|Yes| bound
    bound -->|"Forwards validated and requested"| forwards
    forwards -->|"All forwards connected"| ready
    forwards -->|"Forward failure"| degraded
    ready -->|"Member lost or forward failure"| degraded
    ready -->|"Release or expiry"| ended
    degraded -->|"Release or expiry"| ended
```

Conditions reuse the existing `LeaseConditionType` values — `Pending`,
`Ready`, `Unsatisfiable`, `Invalid` — with `ForwardsReady` and `Degraded`
added. `Ready` for a member-form lease requires all required members bound and every
non-disabled forward connected. A forward whose optional endpoint was omitted
has explicit `Disabled` status and does not gate readiness. Expiry,
`status.ended`, the `jumpstarter.dev/lease-ended` label, and `spec.release`
retain their existing behavior.

### Forward validation and establishment

Validation happens after binding, because the report that proves a port
exists belongs to a bound exporter, not to a selector (DD-12). For each
`spec.forwards[]` entry the controller checks, against
`ExporterStatus.Devices[].Ports`:

1. Every endpoint names a declared member — enforced by CEL at admission,
   before this point. If either role is an omitted optional member, validation
   stops for that entry and records `Disabled` with the omitted role named.
2. Both named ports exist on the respective bound exporters, and each port
   resolves to one driver UUID because report registration enforces
   exporter-wide unique names.
3. **Direction resolves.** For a `between` forward, exactly one endpoint must
   report `PROVIDES` and the other `REQUIRES`; the controller assigns the
   roles accordingly (DD-13). For an explicit `src`/`dest` forward, the stated
   roles must match what the exporters report.
4. Both ports have attachment `socket`; `wired` and `wireless` ports cannot
   be forwarded in this JEP (DD-14).
5. If both ports declare `protocol`, the values are equal (DD-8).

A failure sets `Invalid`, names the forward and reason, and leaves members
bound for inspection. A disabled optional-member forward is not a validation
failure and receives no setup instruction or token.

Setup follows *How a forward comes up*. The controller derives a router
subject from `(lease UID, forward name)` using UUIDv5 in a fixed namespace,
keeping it stable across reconciles. Both tokens use that value as `sub`, use
`aud: https://jumpstarter.dev/router`, carry reciprocal member, exporter, and
side claims, and expire no later than the lease. `RouterService.Stream`
validates those claims as described in DD-5.

The direct attempt has a short, bounded timeout and is not raced with a
router connection. Direct mode requires controller-issued per-forward mTLS:
both sides validate the peer certificate identity before the requiring side
transmits `peer_token` inside the encrypted channel. A plaintext or
server-authentication-only connection is rejected. `Direct` fails without
fallback; `Router` skips the direct attempt. Status records the transport
used and the reason for any fallback.

**Direct eligibility.** Both members must report the same non-empty
`NetworkZone`, and the `provides` side must report a `PeerEndpoint`. The zone
is an opaque value supplied by deployment configuration, such as one value
per cluster network. The controller does not infer reachability from IP
addresses. An unreachable peer falls back to the router in `Auto` mode.

**Reconnection.** The `requires` endpoint keeps its listener open and
re-establishes the peer path with backoff after a network interruption, router
restart, or ingress reload. One accepted local TCP connection and one peer
stream form a single splice: if the peer stream closes, the exporter closes
that local connection and never attaches a replacement stream to it. A driver
that reconnects gets a fresh splice. Reconnect events invoke an explicit
endpoint-driver recovery hook for protocols that dial only once or must
restore state, such as restarting a projection server. Forward state comes
from the stream, without probing the service.

**Failure modes and handling:**

| Failure | Behavior |
| --- | --- |
| Named port absent on a bound exporter | Lease `Invalid`; members stay bound for inspection |
| Both endpoints `provides`, or both `requires` | Lease `Invalid`; direction cannot resolve (DD-6, DD-13) |
| Explicit `src`/`dest` contradicts the reported directions | Lease `Invalid` naming the forward and the reported roles |
| Declared protocols disagree | Lease `Invalid` (DD-8) |
| A port's attachment is `wired` or `wireless` | Lease `Invalid` naming the port and its attachment (DD-14) |
| Protocols differ and at least one tag is absent | Validation passes; protocol errors may occur when data is exchanged (DD-8) |
| Forward references an undeclared member | Rejected by CEL at admission; lease never created |
| Forward references an omitted optional member | Forward `Disabled` naming the role; no setup or token; lease may become `Ready` |
| Duplicate port names in one exporter's reports | Exporter registration rejected with `INVALID_ARGUMENT` |
| `listen` address already bound on the exporter | Lease `Invalid` naming the port and address |
| Router stream drops mid-lease | Re-dial with backoff; `Reconnecting`; `Degraded` after a grace period |
| Ingress/proxy reload cuts the peer stream | Reconnect with backoff and notify endpoint drivers |
| Direct dial fails or times out | In `Auto`, fall back to the router and record the reason; in `Direct`, fail |
| A member's exporter disappears | Peer's stream resets; lease `Degraded` naming the role (DD-3) |
| Client releases the lease | Forwards torn down first, then the lease ends normally |

### Reference drivers for projection

The reference drivers handle device setup and protocol recovery:

- **`cuttlefish` (phone).** Prepare the image with ADB enabled, the exporter
  key installed, and the Bluetooth profiles needed by the test. These are
  per-image preparation steps. Use `cvd restart` for recovery that retains
  userdata. For `projection-wifi`, disable the guest's Ethernet connection,
  join the instance's AP, and configure the route to the guest. Start the
  projection server on request and restart it on forward-reset events.
- **`projection-rx` (head unit).** Run the receiver with a display, dummy
  audio device, and open console for input commands. Capture screenshots
  from the display. Start the receiver on a client call after the forward
  listener is available, since it dials immediately on startup.

- **`bt-peer` (phone).** Add a `profiles:` list to configure HFP Audio
  Gateway alongside A2DP, and persist the bond keystore for the lease lifetime
  so restarting the peer does not invalidate the DUT's link key.

The drivers configure only local endpoints. The phone exposes named ports;
`projection-rx` dials its local listener, such as `127.0.0.1:5277`. The lease
specifies the connection between them.

### Concurrency and ordering

Reconciliation is single-writer per lease (standard controller-runtime work
queue), so no intra-lease locking is needed, and member selection is pure
computation followed by one write. Forward splicing on the exporter side runs
in the existing per-driver task group, so a stalled forward cannot block
driver calls on other children.

For client-started drivers such as `bt-peer`, the listener must be available
before `start()` dials it. Shared-rootcanal guests need the forward before
boot, while federated rootcanals need a join after establishment (DD-9).
Provisioner ordering remains an unresolved question.

### Security

- **Access policy:** evaluate each member against `ExporterAccessPolicy` as
  for a direct request (DD-12).
- **Forward authorization:** `DialPeer` verifies that the caller is the
  exporter bound to the named member and that the forward includes it.
  Tokens expire with the lease. Access is limited to the explicitly named
  peer and port.
- **Port exposure:** only declared ports can participate in forwards. A
  `requires` listener uses an exporter-configured address and exists only
  while leased.
- **DUT exclusivity:** holding an exclusion group does not grant access to
  the group's unbound exporters. They cannot be dialed through the lease
  and do not receive `status.leaseRef`, so they cannot release it. Only
  administrators assign groups; an exporter cannot join a group through its
  own registration labels, so it cannot block unrelated exporters (DD-16).
- **Direct authentication and confidentiality:** the optional peer listener
  is disabled by default and accepts only controller-issued, short-lived
  per-forward mTLS credentials. Both sides verify the expected exporter and
  member identity from the certificate before `peer_token` is sent inside
  the encrypted channel. It is separate from device ports. Network policies
  should allow the authenticated peer port while blocking peer access to
  unauthenticated simulator ports. JEP-0016 is expected to supply this policy
  with exporter Pods.
- **Membership:** `members` is immutable after creation.
- **Physical RF:** devices in a shared lab are audible to others in range;
  lease authorization does not isolate radio traffic.
- **Simulator control:** standalone rootcanal listens on `0.0.0.0` without
  client authentication. Keep its test channel private because it can
  re-address devices, join controllers, and trigger the crash described in
  DD-9. Drivers may expose HCI and link ports through authorized forwards;
  network policy must block direct access to the underlying ports.

### Observability

JEP-0013 telemetry adds `lease.member` alongside `lease.name` and records
member count on the lease-acquisition metric (creation to `Ready`).
Per-forward telemetry records bytes in each direction, reconnect count,
selected transport, and direct-dial fallback rate. Same-zone fallback can
indicate an unavailable peer listener, incorrect zone configuration, or a
blocking `NetworkPolicy`.

Reconnects also emit events for drivers that must restore protocol state,
such as the phone's projection server. Forward status exposes reconnection
and degradation.

Where netsim supplies the simulated medium, `jumpstarter-driver-netsim` can
start, stop, and download pcap captures through its REST control API. These
captures can be attached to test results alongside forward metrics.

## Backward Compatibility

The schema and protocol changes are additive. DD-2 defines compatibility
for `status.exporterRef`.

- **CRD**: `Lease` gains two optional spec lists and three optional status
  lists (`members`, `forwards`, `exclusionGroups`); `ExporterStatus.Devices[]`
  gains an optional `ports` list, and `ExporterStatus` gains optional
  `peerEndpoint` and `networkZone`. No existing field changes type, meaning,
  or default. `Exporter` gains one optional spec field, `exclusionGroup`;
  exporters without it behave as today. `ExporterAccessPolicy`, `ExporterSet`, and `VirtualTargetClass` are
  untouched. Every lease that exists today validates unchanged.
- **`status.exporterRef`**: unchanged for every lease that does not pass
  `members`. Every member-form lease, including an explicit one-member list,
  leaves it nil, which existing consumers already read as "not bound yet"
  (DD-2), so the JEP-0016 façade, `jmp get leases`, `Dial` and JEP-0013
  telemetry keep working; they change only to *support* member-form leases.
- **Driver report and drivers**: `ports` is a new optional repeated field, so
  an exporter built before this JEP reports none and is treated as
  unable to participate in forwards. An unset `attachment` means `socket`.
  Ports are declared in exporter configuration; `bt-peer` needs no Python
  changes to use a forwarded HCI endpoint.
- **Protocol**: new fields on existing messages and one new RPC.
  Unknown fields are ignored by proto3, so an N-1 client talks to an N
  controller unchanged. An N client requesting `members` from an N-1
  controller has them silently dropped — so the client probes for `DialPeer`
  (or a controller version) and fails with a clear message rather than
  acquiring a one-device lease it will misuse.
- **Operator upgrade**: a CRD schema addition, a standard bundle bump with no
  conversion webhook. Member-form leases must be removed before rollback;
  scalar leases remain compatible.
- **Coexistence**: scalar- and member-form leases share one exporter pool,
  one scheduler, and one selection implementation.

## Consequences

### Positive

- One lease acquires all required members in one status write and gives them
  a shared lifetime.
- Exporters can run on different hosts while retaining role-based access,
  lease policy, and telemetry.
- Forwards reuse existing stream primitives and `RouterService`; named ports
  are discoverable through exporter reports.
- The port model supports Bluetooth, projection, CAN, and serial connections
  without adding protocol-specific logic to the controller.

### Negative

- Consumers must handle scalar and member-list lease forms.
- Port declarations add configuration, and fixed listeners depend on network
  namespace isolation.
- Bind-time port validation holds devices even when a forward is invalid.
  Omitted protocol tags allow compatibility errors to surface at runtime.
- Individual members cannot be released early.
- Forwarding adds local listeners and, optionally, an authenticated peer
  listener to exporters.
- Timing-sensitive protocols and Wi-Fi simulation require further testing
  and may need upstream changes.

### Risks

- **Wi-Fi transport:** vhost-user requires a frame bridge, and TCP
  head-of-line blocking may prevent reliable medium simulation. Phase 4 may
  require direct mode or separate datagram support (DD-10).
- **Projection artifacts:** the phone image, app, and receiver must be
  supplied by the lab. Phase 2 CI needs an artifact source that does not
  require project redistribution. Drivers accept artifact locations as
  configuration.
- **Bluetooth latency:** single-node pairing results do not establish
  cross-node or sustained A2DP performance. Publish workload-specific
  latency measurements.
- **Simulator failures:** the rootcanal test-channel crash and guest recovery
  behavior require private control ports, explicit health reporting, and
  driver-owned recovery (DD-9).
- **Stream interruption:** ingress reloads and router restarts can cut
  long-lived forwards. Endpoints reconnect and emit recovery events; tests
  must cover deliberate stream loss. Direct connections avoid the ingress
  path where available.
- **Deployment isolation:** shared network namespaces can cause listener
  collisions and simulator resets across leases. Enforce the documented
  deployment assumptions and report bind failures clearly.
- **Binding contention:** leases with more members have more opportunities
  to hit the existing cross-lease race. `MaxItems=8` bounds member count, and a later
  reconcile rebinds a conflicting lease (DD-1).
- **Consumer compatibility:** readers may assume every bound lease has
  `status.exporterRef`. Existing consumers must be checked before release.
- **Upstream interfaces:** netsim and vhost-user integration may change.
  Pin runtime images and track upstream compatibility.
- **Capacity:** per-member policy and the eight-member limit bound access;
  there is no quota across a lease's members.

## Rejected Alternatives

DD-1 through DD-17 record the API and transport alternatives. Higher-level
alternatives are:

- **Keep client-managed leases:** leaves partial acquisition, independent
  lifetimes, and unmanaged device connections.
- **Add `LeaseGroup` or `LeaseSet`:** child leases require partial-acquisition
  recovery (DD-1). `LeaseSet` also suggests the interchangeable replicas of
  `ExporterSet`, rather than members with distinct roles.
- **Lease devices independently inside one exporter:** without an inventory
  of the DUTs and their shared harness, this could divide control of a
  physically connected assembly. A composite DUT behind one exporter remains
  supported (DD-15).
- **Build a multi-device test runner:** existing runners can consume the
  leased devices. This proposal supplies allocation and connectivity.
- **Adopt a mobile test framework as the fleet layer:** this would require
  adapting its device and allocation model to Jumpstarter.

## Prior Art

- **LAVA MultiNode** assigns named device roles within one job and provides
  `lava-sync`, `lava-send`, and `lava-wait` for test-script coordination.
  It is a reference for grouping devices within the existing work unit.
- **Mobly** provides the testbed format used by the proposed export command.
- **Cuttlefish multi-instance connectivity** supplies the local simulator
  interfaces considered in DD-9 and DD-10.
- **Android emulator networking** provides another model for multi-device
  connectivity within one host.
- **Bumble** supplies virtual hosts, controllers, and link relays. The existing
  `jumpstarter-driver-bt-peer` demonstrates the required-port model.
- **Kubernetes gang scheduling** illustrates the coordination needed when
  claims live on separate objects. Lease member claims instead live in one
  status update, with the cross-lease race described in DD-1.
- **`kubectl port-forward`** provides a comparable byte-transport guarantee
  without checking application protocol compatibility.

## Unresolved Questions

To resolve during review:

- **Bumble controller:** validate option 3 from DD-9 with Cuttlefish.
- **Link-layer setup:** decide whether rootcanal join and address assignment
  run through a driver post-establish hook, a lease-level action, or the
  test. A driver hook keeps Bluetooth handling outside the controller.
- **Launch ordering:** define how the provisioner waits for a shared HCI
  forward before booting the second CVD, and how federation runs its join
  after the link forwards are available.
- **Readiness:** distinguish a forward listener being available from an
  application connection being established. Client-started drivers need
  the former before they can create the latter.
- **Listener allocation:** keep fixed addresses with collision validation,
  or allocate ephemeral ports and pass the address to the driver.
- **Synchronization:** determine whether client-side barriers are sufficient
  or independently controlled roles need a controller-mediated barrier.
- **Mobly endpoints:** decide whether exported ADB endpoints depend on a live
  shell session or on longer-lived client-managed forwards.

To resolve during implementation:

- The direct-dial timeout before router fallback.
- How deployments supply and validate `NetworkZone`; the proposed default
  is deployment configuration.
- Protocol-specific reconnect behavior, including projection-server restart
  and persistence of Bumble bond keys.
- How Phase 2 CI obtains vendor artifacts without redistributing them.
- Readable multi-member output in `jmp get leases`.

## Future Possibilities

The following work builds on this JEP:

- **Shared infrastructure exporters:** exporters that serve several other
  exporters and leases, such as network switches, relay matrices, signal
  gateways, RF enclosures, and programmable power supplies. Such an exporter
  maps its channels to other exporters' ports, allocates them per lease, and
  isolates them at release. This work would allow forwards over `wired` and
  `wireless` ports, including buses with more than two endpoints and a
  fidelity level that separates electrical connections from frame-forwarding
  gateways. It would also add scoped access to shared channels, such as one
  power output (DD-14).
- **Co-location constraints:** let a member require placement in the same
  RF domain or harness as another member, so that a lease can choose any
  rack where both roles fit instead of naming one.
- **`Device` resource:** an inventory of the DUTs behind each exporter,
  each with its own firmware, software, and configuration. Lease members
  could select a DUT by its installed software, and a DUT that does not
  share a harness could be leased on its own. The lease format already
  supports this addition (DD-15): a device-scoped source can join the
  selection `oneof`, member status can gain a device reference, a `Device`
  can own the ports of the drivers under it, and binding stays all-or-nothing
  with one status write. A `Device` could also list the exporters attached to
  it, replacing `spec.exclusionGroup` with the device name (DD-16). That work
  must reconcile the name with the existing `ExporterStatus.Devices[]`.
- **Mixed radio leases:** a gateway exporter owns a physical radio adapter
  and exposes it as a provided port. Decide whether it is a separate leased
  member or part of the physical device's exporter, and how the shared RF
  resource is allocated (DD-11).
- **Selection-time port validation:** expose reported ports through JEP-0017
  labels so an unsatisfiable request holds no exporters (DD-12).
- **Lease templates and polyglot codegen:** a lease template is a
  language-neutral file for a reusable declaration. It has the lease's
  `members` and `forwards`, plus a client-only `drivers` map per member from
  driver name to JEP-0011 proto package:

  ```yaml
  kind: LeaseTemplate
  metadata: { name: projection }
  spec:
    members:
      - name: phone
        selector: { matchLabels: { device-type: phone } }
        drivers: { adb: jumpstarter.driver.adb.v1, bt_peer: jumpstarter.driver.bt_peer.v1 }
      - name: headunit
        selector: { matchLabels: { device-type: headunit } }
        drivers: { power: jumpstarter.driver.power.v1 }
    forwards:
      - { name: bt, between: [{ member: headunit, port: rootcanal }, { member: phone, port: controller }] }
  ```

  The codegen pipeline that produces per-interface clients from JEP-0011
  protos can generate the typed lease from it: the Python `Projection`
  class above, or a TypeScript, Java, Kotlin, or Rust type with one
  accessor per role and per forward. Names follow each language's
  convention, for example `bt_peer` becomes `btPeer`. A template can also
  be emitted from a Python class, as JEP-0011 does for interfaces, and can
  be applied from the CLI (`jmp create lease --template projection`).
  Generated clients request the lease with `RequestLeaseRequest` and dial
  each role with `DialRequest.member_name`.
- **Global scheduling:** resolve the existing cross-lease binding race.
- **Fan-out forwards:** connect one provided port to several required ports,
  including shared media such as Bumble relay rooms.
- **Ephemeral listeners:** allocate addresses dynamically if shared network
  namespaces are supported later.
- **Multi-exporter policy and quota:** add limits across members and support individual
  member release if needed.
- **Kubernetes-native mTLS identity:** Kubernetes 1.37 graduates Pod
  Certificates and ClusterTrustBundles to Stable. Exporter Pods could mount a
  signer-issued, automatically rotated X.509 identity and trust bundle for
  direct peer, controller, and router mTLS. This would keep the workload
  private key generated and managed by the kubelet instead of returning a
  private key from `DialPeer`; the per-forward token would remain as
  lease-scoped authorization after workload authentication. Adoption requires
  a configured signer (Kubernetes 1.37 does not ship a production signer in
  core), live certificate reload, an identity-to-`Exporter` binding, and a
  platform-neutral fallback for exporters outside Kubernetes.
- **Broader exporter mTLS:** use mTLS for exporter-to-controller and
  exporter-to-router connections. Unlike the current bearer token, it proves
  possession of a private key while protecting the channel, and it can bind
  the certificate identity to one `Exporter`. Lease and forward
  authorization would still apply. Physical and non-Kubernetes exporters
  need an issuer and enrollment path.
- **Datagram transport:** define a separate protocol extension for framed
  traffic, avoiding TCP head-of-line blocking in the Phase 4 bridge.
- **Vehicle-bus simulation:** integrate a restbus simulator as a provided
  socket alongside existing CAN, DoIP, SOME/IP, UDS, XCP, and OBD drivers.
- **Test-framework integration:** supply a device or Mobly-controller shim
  backed by a lease.
- **On-demand members:** provision JEP-0014 pool instances to satisfy a lease.

## References

- [JEP-0014: Virtual Scalable Exporters](JEP-0014-virtual-scalable-exporters.md)
  — "Composite leases — multiple exporters linked into one logical lease"
  (Future Possibilities)
- JEP-0016: Cuttlefish Kubernetes-Native Orchestration (draft, not yet
  submitted) — DD-8, whose option 1 this JEP implements
- JEP-0017: Dynamic Exporter Labels (draft, not yet submitted) — the
  mechanism selection-time port validation depends on (DD-12)
- [JEP-0013: Metrics, Tracing, and Log Observability](JEP-0013-observability-telemetry-logs.md)
- [JEP-0011: Protobuf Introspection and Interface Generation](JEP-0011-protobuf-introspection-interface-generation.md)
  — the introspection direction port reporting extends
- Native gRPC Services (draft, not yet submitted) — reserves
  `DriverInstanceReport` field 7 (`native_services`)
- Polyglot Typed Device Wrappers (draft, not yet submitted) — the per-exporter
  codegen pipeline a lease template extends to multi-exporter leases
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
  — `jumpstarter-driver-netsim` (merged); the control-plane companion,
  incl. pcap capture
- [netsim (`platform/tools/netsim`)](https://android.googlesource.com/platform/tools/netsim/) —
  `proto/netsim/packet_streamer.proto`
- [google/android-cuttlefish](https://github.com/google/android-cuttlefish)
- [Kubernetes 1.37: Pod Certificates and Cluster Trust Bundles](https://kubernetes.io/blog/2026/08/28/kubernetes-v1-37-pod-certificates-and-cluster-trust-bundles/)
  — Stable projected workload certificates and trust anchors (KEP-4317 and
  KEP-3257)
- [Test Multi-Device Interactions with the Android Emulator](https://android-developers.googleblog.com/2026/04/Test-Multi-Device-Interactions-with-the-Android-Emulator.html)
- [LAVA MultiNode](https://docs.lavasoftware.org/lava/multinode.html)

---

*This JEP is licensed under the
[Apache License, Version 2.0](https://www.apache.org/licenses/LICENSE-2.0),
consistent with the Jumpstarter project.*
