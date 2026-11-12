# JEP-0015: Multi-Exporter Leases and Inter-Exporter Port Forwarding

| Field             | Value                                                    |
| ----------------- | -------------------------------------------------------- |
| **JEP**           | 0015                                                     |
| **Title**         | Multi-Exporter Leases and Inter-Exporter Port Forwarding |
| **Author(s)**     | @kirkbrauer (Kirk Brauer, kbrauer@hatci.com)             |
| **Status**        | Discussion                                               |
| **Type**          | Standards Track                                          |
| **Created**       | 2026-09-01                                               |
| **Updated**       | 2026-11-12                                               |
| **Discussion**    | [PR #1069](https://github.com/jumpstarter-dev/jumpstarter/pull/1069), [issue #47](https://github.com/jumpstarter-dev/jumpstarter/issues/47) |
| **Requires**      | JEP-0014                                                 |
| **Supersedes**    |                                                          |
| **Superseded-By** |                                                          |

---

## Abstract

This JEP extends a `Lease` to acquire multiple exporters together and to
forward socket traffic between their named driver ports. Optional
`spec.members[]` assigns roles to exporters, `spec.constraints[]` requires
members to share a label value such as a CAN bus or RF domain, and
`spec.forwards[]` declares connections between them without exposing local
addresses.

Lab administrators can publish these definitions as a namespaced
`LeaseTemplate`, which expands into an ordinary multi-member lease when a
client requests it by name. Client libraries request multi-member leases
only through templates; ad-hoc members are a `jmp` CLI feature (DD-20).

A lease binds every required member or none, the API server ensures that
no two leases ever hold the same exporter (DD-28), and the members share a
lease lifetime.

Forwards reuse the existing port-forwarding primitives and router, with
mutually authenticated, encrypted direct connections preferred within a
network zone. The same lease serves virtual devices, where forwards carry
the simulated radio medium between Pods, and physical devices, which share
a real radio or cable while the lease coordinates their access.
Forwards carry TCP streams only. Ports that describe a physical cable or
radio, or that carry UDP or multicast traffic such as SOME/IP service
discovery, are reported but cannot be forwarded (DD-14, DD-26).

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

Even with a combined lease, the knowledge of which exporters belong
together lives with the lab, not the test. Which head unit shares a CAN bus
with which ECU, which ports join them, and which labels select them differ
between labs and change as benches are rewired. If every test repeats that
knowledge as selectors and port names, a lab change breaks every test that
copied it. The same test also cannot run in a second lab whose labels
differ. A test should name the setup it needs and let the lab define it.

### Virtual and physical devices

Leases of virtual and physical devices use the same members, exclusivity,
and lifetime. They differ in where the medium between devices lives, and so
in what a forward carries:

| | Virtual devices | Physical devices |
| --- | --- | --- |
| **Example** | A Cuttlefish phone and head unit in separate Pods | A real phone and head unit on two lab hosts |
| **Medium between devices** | Simulator sockets on each exporter: rootcanal HCI, netsim, `wmediumd` | A real radio or cable |
| **What joins the devices** | A forward carries the simulated medium | The air or the harness; the lease coordinates access |
| **What forwards carry** | Bluetooth HCI and link layer; projection over ADB or the guest's Wi-Fi address | Only TCP traffic bridged to a socket, such as CAN through `socketcand` or a serial bridge; not UDP or multicast, such as SOME/IP service discovery (DD-26) |
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
- **As a** lab administrator, **I want to** publish a named definition of
  a head unit, a phone, and the body ECU on the head unit's CAN bus, with
  its wiring and documentation, **so that** users lease a correctly wired
  set by name without knowing our labels or port names.
- **As a** test engineer, **I want to** reference that definition by name
  from my test, **so that** the same test runs in every lab that publishes
  a definition with the same name and roles.

## Proposal

### Scope

This JEP adds seven concepts, two of them new resources:

| Concept | Where | What it does |
| --- | --- | --- |
| **Members** | `Lease.spec.members[]` | Role names, each with the existing `selector` / `exporterRef`. A lease binds every required member or holds none. |
| **Constraints** | `Lease.spec.constraints[]` | Require listed members to bind exporters that carry the same value of one label, such as `bus` or `rf-domain` (DD-22). |
| **Ports** | Exporter config, reported in `DriverInstanceReport` | Named connection points a driver **provides** (a local service) or **requires** (a socket it dials). A third direction, **connects**, marks an undirected port: two of them form one bidirectional path (DD-27). Addresses never leave the exporter. |
| **Forwards** | `Lease.spec.forwards[]` | Join a provided port on one member to a required port on one or more others. Each connection gets its own stream over the existing router, or a direct connection within a network zone. |
| **Exclusion groups** | `Exporter.spec.exclusionGroup` | Set by an administrator on exporters that serve one DUT; at most one lease holds the group (DD-16). |
| **Claims** | New `ExporterClaim` resource | One per held exporter or exclusion group, named after what it claims, so the API server allows only one holder (DD-28). Only the controller writes them. |
| **Lease templates** | New `LeaseTemplate` resource | An admin-managed, namespaced definition of members, constraints, forwards, defaults, and documentation. A request by name expands it into a member-form lease (DD-19). |

Member-form leases are requested in two ways: through a `LeaseTemplate`,
from any client, or with the `jmp` CLI's ad-hoc `--member` flags (DD-20).
This is a boundary of the client API, not an authorization rule
(*Security*). The client libraries do not expose ad-hoc members. A lease with
members appears in `jmp get leases` and uses the existing expiry and
`spec.release` behavior. Scalar leases are unchanged.

**In scope:**

- All-or-nothing binding of up to eight members, with per-member access
  policy (DD-1, DD-12).
- Co-location by shared label value, chosen during binding (DD-22).
- Exclusive access to a DUT that several exporters serve, through an
  admin-assigned exclusion group (DD-16).
- Claims enforced by the API server, so no two leases ever hold one
  exporter or exclusion group, including in small, busy pools (DD-28).
- TCP byte forwards from one provided socket port to one or more required
  ports, and undirected paths between two `connects` ports, with direction
  checks and an optional protocol tag (DD-4 to DD-8, DD-23, DD-27).
- `LeaseTemplate` definitions, discovery, and expansion, with request-time
  narrowing and exclusion of optional members (DD-19 to DD-21).
- CLI support for templates and ad-hoc member-form leases; client and
  `JumpstarterTest` support for templates and existing leases, with typed
  member classes (DD-17); per-role shell commands and metadata (DD-24); and
  export of a lease as a testbed file for Mobly, Google's multi-device test
  framework.
- Reference topologies, delivered in order: Phase 1 virtual Bluetooth,
  Phase 2 virtual projection, Phase 3 physical devices across hosts, and
  Phase 4 virtual Wi-Fi.

**Not covered, and how this JEP behaves instead:**

| Not covered | Behavior in this JEP |
| --- | --- |
| Switching physical links (Ethernet, CAN, LIN, FlexRay) through relays, switches, or signal gateways | `wired` and `wireless` ports are reported; a forward that names one fails validation (DD-14) |
| Placement rules other than a shared label value, such as expressions, anti-affinity, or measured RF range | Members can be required to share one label value (DD-22) |
| Shared power supplies and other devices serving several exporters | Not modeled |
| Pairing a virtual device with a physical radio | Not supported (DD-11) |
| UDP and other datagram traffic, multicast, and L2 segments, such as SOME/IP service discovery between ECUs; forwards that copy traffic between requiring ports | TCP byte streams, each between one provided and one required port, or along one path between two `connects` ports. Ports report their direction and transport, so `datagram` and `frame` ports can be discovered; a forward that names one, or more than two `connects` ports, fails validation (DD-23, DD-26, DD-27) |
| Rejecting unsuitable exporters during selection | Ports are validated after binding (DD-12) |
| Leasing one DUT out of several on an exporter | Members bind whole exporters (DD-15) |
| Declaring members, constraints, or forwards in test code or client libraries | Libraries request a template by name or attach to an existing lease (DD-20) |
| Adding roles or forwards to a template at request time | Requests may only narrow selectors, exclude optional members, and set duration and metadata (DD-21) |

*Future Possibilities* describes work that builds on this JEP.

### Ports

A port is a named connection point on a driver. Each forward connects one
`provides` port to one or more `requires` ports.

| Direction | Meaning | Example |
| --- | --- | --- |
| `provides` | A service is listening; something may be forwarded *from* it | `rootcanal` on a Cuttlefish exporter — HCI on `127.0.0.1:7300` |
| `requires` | The driver will dial a local address; a forward may be delivered *to* it | `controller` on a `bt-peer` exporter — where its bumble stack expects an HCI controller |
| `connects` | Neither side leads; two such ports form one bidirectional path | Two serial-over-TCP bridges on different benches joined as a cross-over (DD-27). Later, an L2 segment that several members join |

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

A socket port also has a `transport`: `stream` (TCP, the default),
`datagram` (UDP, unicast or multicast), or `frame` (link-layer frames, such
as Ethernet through a tap device). Forwards in this JEP carry `stream`
ports only. A SOME/IP service discovery endpoint, for example, is a
`connects` port with transport `datagram`. Drivers report it so that it
can be discovered, and a forward that names it fails validation (DD-26).

### Declaring a lease template

A lab administrator declares which exporters belong together, and how they
are wired, in a namespaced `LeaseTemplate`:

```yaml
apiVersion: jumpstarter.dev/v1alpha1
kind: LeaseTemplate
metadata:
  name: projection
  namespace: jumpstarter-lab
spec:
  description: "Virtual phone and head unit sharing one Bluetooth controller"
  docs:
    - https://wiki.example.com/labs/projection
  instructions: |
    Power the head unit on before starting the phone's Bluetooth peer.
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
      endpoints:
        - { member: headunit, port: rootcanal }
        - { member: phone,    port: controller }
  defaults:
    duration: 45m
```

`members`, `constraints`, and `forwards` use exactly the schema of the
corresponding `Lease` fields. The template names members and ports.
Exporters resolve addresses locally, and the controller determines direction
from reported ports at bind time (DD-6, DD-13). Each member uses the existing
`selector` or `exporterRef` fields with a role name.

The other fields are for people and tools, not the scheduler:

- `description`, `docs`, `image` (a link to a photo or diagram), and
  `instructions` are shown by `jmp describe lease-template`. The controller
  never fetches `docs` or `image`.
- `defaults` supplies values that a request may override, currently
  `duration`.

A template does not describe the drivers its members must have. That
contract belongs to the ExporterClass proposal, whose `DriverInterface`
and `ExporterClass` resources describe an exporter's driver tree. Once it
lands, each member can pin its exporter to a class (*Future
Possibilities*).
Until then, the typed Python class states the drivers a test expects, and
`connect()` checks them (DD-17).

Administrators manage templates with `kubectl apply` or with
`jmp admin apply -f`, which gains the `LeaseTemplate` kind. Clients can list
and read the templates in their namespace through `ClientService`, but
cannot create or change them.

A member marked `optional: true` may be omitted at binding; its role then
resolves to `None` on the client. A forward that names an omitted member
serves its other endpoints if they still include the providing port and at
least one required port. Otherwise it is `Disabled`, with a status message
naming that member. A disabled forward is not set up and does not prevent
the rest of the lease from becoming `Ready`. If the optional member binds,
every forward that names it is set up normally.

A request can also exclude an optional member up front with `--without`.
The expanded lease keeps the member, marked `excluded: true`, and the
controller never binds it: its state is `Omitted` with reason `Excluded`.
Forwards and constraints are copied unchanged and treat it as they treat
any omitted member (DD-21). Excluding a required member is rejected.

#### Co-location: a shared bus and RF domain

A physical head unit, a phone in radio range of it, and a body ECU on the
head unit's CAN bus. The lab labels each exporter with the bus and RF domain
it sits on, and the template requires matching values instead of naming
one bench:

```yaml
apiVersion: jumpstarter.dev/v1alpha1
kind: LeaseTemplate
metadata:
  name: ivi
  namespace: lab-madrid
spec:
  description: "AAOS head unit, phone, and body ECU on a shared CAN bus"
  image: https://wiki.example.com/labs/ivi.png
  members:
    - name: headunit
      selector: { matchLabels: { device-type: aaos-headunit } }
    - name: phone
      selector: { matchLabels: { device-type: phone } }
      optional: true
    - name: ecu
      selector: { matchLabels: { device-type: ecu } }
  constraints:
    - { members: [headunit, ecu],   sameLabel: bus }
    - { members: [headunit, phone], sameLabel: rf-domain }
  defaults:
    duration: 2h
```

The controller chooses the label value during binding: any bus whose head
unit and ECU are both free will do (DD-22). Every member in a constraint must
carry the label, and an omitted optional member is ignored by its
constraints. The devices share a real radio and a real cable, so the template
declares no forwards (DD-14).

Exporters on different hosts can still be joined by a socket bridge. Two
ECUs can share a CAN segment through `socketcand` or another TCP bridge:

```yaml
spec:
  members:
    - name: gateway
      selector: { matchLabels: { ecu-role: gateway } }
    - name: node
      selector: { matchLabels: { ecu-role: body-controller } }
  forwards:
    - name: powertrain-bus
      endpoints:
        - { member: gateway, port: can0 }
        - { member: node,    port: can }
```

The endpoints may be listed in any order. The controller checks that the
ports exist, that exactly one provides and every other one requires, and
that any declared protocols agree (DD-13). Two `connects` ports form an
undirected path instead (DD-27).

One provided port can serve several members. Each required port opens its
own connections to the provided service:

```yaml
  forwards:
    - name: console
      endpoints:
        - { member: dut,      port: console }   # provides
        - { member: logger,   port: console }   # requires
        - { member: debugger, port: console }   # requires
```

Naming the same provided port in several forwards has the same effect and
gives each required port its own status entry. A `requires` port has one
listener, so it can appear in only one forward (DD-23).

When neither end is a provider, both ports declare `connects` and the
forward is one undirected path. Here, two serial-over-TCP bridges on
different benches are joined as a cross-over:

```yaml
  forwards:
    - name: uart
      endpoints:
        - { member: dut,       port: uart }   # connects
        - { member: companion, port: uart }   # connects
```

Each exporter configures its `connects` port as it would a provided or
required one: the exporter dials a local service, such as a bridge's TCP
server, or listens for a local client. That choice stays local, and the
other end does not need to know it (DD-27).

### Acquiring and using a multi-exporter lease

Templates are discovered and requested through the existing commands:

```console
$ jmp get lease-templates
NAME         MEMBERS                  AVAILABLE   DESCRIPTION
ivi          headunit, phone*, ecu    2/3         AAOS head unit, phone, and body ECU…
projection   phone, headunit          2/2         Virtual phone and head unit sharing…

$ jmp describe lease-template ivi       # members, constraints, matching exporters, docs, image link
$ jmp shell -t projection               # --lease-template projection
$ jmp create lease -t ivi --duration 4h --without phone --member headunit=soc=sa8775p
ivi-4fj2k
```

`*` marks an optional member. `AVAILABLE` counts members that have at least
one exporter this client could lease now; it is advisory and does not run
the joint assignment or constraints. On a request, `--duration`,
`--begin-time`, `--tag`, and `--context` override the template's defaults,
`--member ROLE=SELECTOR` adds requirements to an existing role's selector,
and `--without ROLE` excludes an optional role. `-l`/`--selector` and
`-n`/`--name` name no role and are rejected with `-t` (DD-21).

The CLI also builds a member-form lease without a template, for exploration,
debugging, and one-off wiring:

```console
$ jmp create lease \
    --member phone=device-type=phone \
    --member headunit=device-type=headunit \
    --forward bt=headunit.rootcanal,phone.controller \
    --duration 45m
projection-9m2qd

$ jmp get leases projection-9m2qd
NAME               SELECTOR   EXPIRES AT            REMAINING   CLIENT      EXPORTER
projection-9m2qd              2026-09-01 10:45:00   44m         ci-runner   phone=rack3-phone-4,
                                                                            headunit=virt-hu-7b2c
```

`SELECTOR` is empty for a member-form lease. The `TAGS` and `SHARED WITH`
columns are omitted here.

`--same-label KEY=ROLE,ROLE` adds a constraint. This is the only ad-hoc
path: client libraries request templates or attach to an existing lease
(DD-20). A setup that tests or pipelines depend on belongs in a template.

Users can discover port names from the exporter report (DD-7):

```console
$ jmp get exporters -l device-type=aaos-headunit --with ports -o json | jq '.exporters[].ports'
[{"name":"rootcanal","direction":"provides","protocol":"hci-h4"}]
```

In a shell, roles become the top-level names of `j`. Forwards have their own
command, `j-forward`, so no role or driver name can collide with it (DD-24):

```console
$ jmp shell --lease projection-9m2qd
jumpstarter ⚡ projection-9m2qd ➤ j phone adb shell getprop ro.product.model
phone-under-test
jumpstarter ⚡ projection-9m2qd ➤ j headunit power on
jumpstarter ⚡ projection-9m2qd ➤ j-forward status
NAME   ENDPOINTS                                MODE     STATE
bt     headunit.rootcanal → phone.controller    direct   Connected
```

The shell also describes the lease's members to its child processes.
`JMP_LEASE` names the lease as today, and `JMP_LEASE_MEMBERS` holds a JSON
object that maps each role to its exporter name, exporter labels, and local
socket; an omitted optional role maps to `null`. `JUMPSTARTER_HOST`,
`JMP_EXPORTER`, and `JMP_EXPORTER_LABELS` describe a single exporter and are
left unset, so `env()` and `env_with_metadata()` fail with a message naming
`env_members()` instead of picking a member (DD-24).

`j-forward status` prints the forward status in the lease. An arrow points
from the providing port to each requiring port, and `↔` joins the two ends
of an undirected path. Byte counts are telemetry (*Observability*), not
lease status.

In Python, a multi-exporter lease is a class bound to a template. Each
member is an attribute annotated with the driver clients the test expects on
that exporter, and each forward is an attribute annotated `Forward`. The
class declares no selectors or endpoints; those come from the template:

```python
from datetime import timedelta

from jumpstarter.client.lease import Forward, LeaseMembers
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


class Projection(LeaseMembers, template="projection"):
    phone: Phone
    headunit: Headunit
    bt: Forward


config = ClientConfigV1Alpha1.load("default")

with config.lease(Projection, duration=timedelta(minutes=45)) as lease:
    with lease.connect() as members:          # members: Projection
        print(members.metadata["phone"].name) # exporter bound to the role
        members.headunit.power.on()           # PowerClient.on
        members.bt.wait_listening(timeout=30) # Forward handle
        members.phone.bt_peer.start_peer('{"name": "Bumble-Phone"}')
        members.phone.bt_peer.wait_connection(timeout=60)
```

A type checker sees `members.phone` as `Phone` and `members.bt` as `Forward`.
It reports an undeclared member, a driver the member's class does not list,
and a wrong call signature. The rules are:

- **Template binding.** `template=` names the template that the class
  requests. The class cannot declare selectors, exporter names, constraints,
  or forward endpoints (DD-20). A class without `template=` can only attach
  to an existing lease.
- **Members.** An attribute annotated with a `CompositeClient` subclass is
  a role. The role is the attribute name with `_` replaced by `-`, or an
  explicit `member(role=...)`. A role that is optional in the template
  must be annotated `| None`, and only such roles may be, so the checker
  forces tests to handle an omitted member.
- **Driver shape.** The annotation is a `CompositeClient` subclass whose
  annotated attributes are driver names typed as client classes. A nested
  `CompositeClient` subclass describes a composite driver, and `| None`
  marks a driver that may be absent; reading an absent optional driver
  returns `None`. Annotations do not change `CompositeClient`'s constructor
  or its child lookup, so the class is an ordinary client of the member's
  exporter. `connect()` checks each bound member against it using the client
  class that the exporter already reports for each driver
  (`jumpstarter.dev/client`), and raises `TypeError` naming the role and
  driver on a mismatch.
- **Forwards.** An attribute annotated `Forward` names a forward in the
  template, with the same `_` to `-` mapping or `forward(name=...)`.
- **Checks before requesting.** The client reads the template and raises
  `TypeError` before creating a lease if the class declares a role or
  forward the template lacks, leaves a required role undeclared, or
  disagrees with the template's `optional` flags. Drivers are checked at
  `connect()`, against the bound exporters. An undeclared optional role is
  excluded, as with `--without`.
- **Dynamic access.** `LeaseMembers.by_role` is a read-only mapping from
  role to client, for generic tooling such as the Mobly export.
- **Metadata.** `LeaseMembers.metadata` is a read-only mapping from role to
  the existing `ExporterMetadata` (exporter name, labels, and lease) of the
  exporter bound to it, with `None` for an omitted role. `connect()` fills
  it from `status.members[]`. Inside a shell it comes from
  `JMP_LEASE_MEMBERS`, and `env_members()` returns the same object (DD-24).

`config.lease` is overloaded on its first argument. A `LeaseMembers`
subclass `M` yields `Lease[M]`, whose `connect()` yields an `M`. The keyword
`template="ivi"` requests a template without a class; its `connect()` yields
a plain `LeaseMembers` whose clients are reached through `by_role`. A
selector string, or the existing `selector=` and `exporter_name=` keywords,
keeps the scalar behavior, and `connect()` yields a single driver client; a
string is never read as a template name. The request form determines the
response shape (DD-2). Request-time narrowing uses the same rules as the
CLI, for example `config.lease(Ivi, member_selectors={"headunit":
"soc=sa8775p"}, duration=timedelta(hours=4))` for a class `Ivi` bound to
the `ivi` template (DD-21).

`config.lease(M, lease_name=...)` attaches to an existing member-form lease,
including one created ad hoc with the CLI. `connect()` then checks `M`
against the lease's members instead of a template.

`JumpstarterTest` accepts a `members` class next to `selector`. Its existing
`client` fixture then yields an instance of that class instead of a single
driver client. Inside `jmp shell`, the fixture attaches to the shell's lease
and checks it against the class; otherwise it requests the class's template:

```python
class TestProjection(JumpstarterTest):
    members = Projection

    def test_pairs(self, client: Projection) -> None:
        client.headunit.power.on()
```

### What the controller stores

A template expands into an ordinary member-form lease when it is requested.
The controller expands the template inside `ClientService.CreateLease`, then
stores the result. The `ivi` request above becomes:

```yaml
apiVersion: jumpstarter.dev/v1alpha1
kind: Lease
metadata:
  name: ivi-4fj2k
  namespace: lab-madrid
spec:
  clientRef: { name: ci-runner }
  templateRef:                       # provenance only; binding never reads it
    name: ivi
    generation: 3
  duration: 4h                       # request overrides the template's 2h
  members:
    - name: headunit
      selector: { matchLabels: { device-type: aaos-headunit, soc: sa8775p } }
    - name: phone
      selector: { matchLabels: { device-type: phone } }
      optional: true
      excluded: true                 # --without phone
    - name: ecu
      selector: { matchLabels: { device-type: ecu } }
  constraints:                       # copied unchanged
    - { members: [headunit, ecu],   sameLabel: bus }
    - { members: [headunit, phone], sameLabel: rf-domain }
status:
  members:
    - { name: headunit, exporterRef: { name: hu-03 },  state: Bound }
    - { name: phone,    state: Omitted, reason: Excluded }
    - { name: ecu,      exporterRef: { name: ecu-11 }, state: Bound }
```

The `rf-domain` constraint is ignored because `phone` is omitted (DD-22).
Binding, member claims, and readiness treat this lease exactly like an
ad-hoc one. Editing or deleting the template does not affect leases already
created from it (DD-19). A one-member template still expands into a
member-form lease, so its role name and response shape stay stable (DD-2).
The requesting client must pass the existing `ExporterAccessPolicy` for
every member; a template grants no access of its own (DD-12).

### Running existing multi-device suites

A lease with ADB-capable members can be exported as a Mobly testbed. The
export runs inside `jmp shell`, and the suite runs in the same shell:

```console
$ jmp shell --lease projection-9m2qd
jumpstarter ⚡ projection-9m2qd ➤ jmp get leases projection-9m2qd -o mobly > testbed.yml
jumpstarter ⚡ projection-9m2qd ➤ mobly_test.py -c testbed.yml --test_bed projection-9m2qd
```

The exported `AndroidDevice` controllers use local ADB endpoints and keep
the member names as device labels, so existing tests and results pipelines
can use them. The shell owns those endpoints: it opens one local ADB forward
per member and keeps it open until the shell exits, and the export only
writes their addresses. A testbed file therefore works only while its shell
is running. Whether exported endpoints should outlive a shell is an open
question (*Unresolved Questions*).

### How a forward comes up

The exporter reuses `TemporaryTcpListener` and `forward_stream()` from
`TcpPortforwardAdapter`, replacing the client stream with a peer stream.
Setup happens once per forward:

1. After binding, the controller validates the ports and sends a setup
   instruction over each endpoint exporter's existing `Listen` stream.
2. The `requires` side binds its configured listener. The `provides` side
   records the forward; it dials its service only when a connection
   arrives. The forward is then `Listening`.

Each connection then gets its own stream, the way a client connection does
today (DD-5):

3. The driver on the `requires` side connects to the listener, and its
   exporter calls `DialPeer`.
4. The controller creates a new stream ID, signs a router token for it,
   returns the token to the caller, and sends the same token to the
   `provides` exporter in a connect instruction over `Listen`.
5. The `provides` side calls `RouterService.Stream` with that token as soon
   as the connect instruction arrives. For a direct-eligible pair, it also
   accepts a direct arrival on its peer listener, and the `requires` side
   first dials that listener. Both sides authenticate with their exporter
   identity certificates (mTLS), and the `requires` side names the
   connection by its stream ID (DD-25). Otherwise, or if that attempt
   fails in `Auto` mode, the `requires` side calls `RouterService.Stream`
   with the token, and the router pairs the two as it pairs a client and
   an exporter (DD-4).
6. The `provides` side dials its local service, and both sides splice their
   local connection to the stream with `forward_stream()`. Closing either
   local connection closes the stream and the other one.

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
| SOME/IP over TCP | `someip` | provides/requires | Unicast TCP between a service and its client; forwardable when endpoints are configured rather than discovered |
| SOME/IP service discovery | `someip-sd` | connects | UDP multicast on a shared segment; transport `datagram`. Reported, not forwardable (DD-26) |
| Serial bridge (pty or TCP) | `uart` | connects | Cross-over between two serial lines, such as a DUT console and a companion on another bench; either end may be a listening bridge or a dialing client (DD-27) |
| GPIO bridge | `gpio` | requires/provides | Interconnects digital pins: one side provides a pin's level changes as a socket stream, and the other applies them to its own pin. The pin itself is a `wired` port (DD-14). Timing is that of the network, not of a wire |

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
dials it. The template `projection-wifi` declares:

```yaml
spec:
  members:
    - name: phone
      selector: { matchLabels: { device-type: phone, projection: "true" } }
    - name: headunit
      selector: { matchLabels: { device-type: projection-receiver } }
  forwards:
    - name: session
      endpoints:
        - { member: phone,    port: projection-wifi }
        - { member: headunit, port: phone }
```

The `projection-wifi` port reaches the server through the guest's Wi-Fi
interface; the `projection` port reaches it over ADB. A lab that offers both
paths publishes one template for each, and the test chooses a path by
choosing a template (DD-13, DD-20). Neither forward alone tests the
Bluetooth-to-Wi-Fi handover (DD-10).

**Physical.** A real phone and head unit pair over the air and project over
the head unit's own Wi-Fi access point. The lease needs no forwards; it
gives the test both devices for the same lifetime. A constraint keeps them
within range without naming a rack:

```yaml
spec:
  members:
    - name: phone
      selector: { matchLabels: { device-type: phone } }
    - name: headunit
      selector: { matchLabels: { device-type: aaos-headunit } }
  constraints:
    - { members: [phone, headunit], sameLabel: rf-domain }
```

The controller binds a phone and a head unit from any one RF domain where
both are free (DD-22). A socket bridge can still carry a wired segment, such
as CAN, to the head unit.

### API / Protocol Changes

The API adds fields to existing types, one resource, and four RPCs:
`GetLeaseTemplate` and `ListLeaseTemplates` on `ClientService`, and
`DialPeer` and `SignExporterCertificate` on `ControllerService`. Existing
fields retain their meanings.

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
  PortDirection direction = 2;     // PROVIDES | REQUIRES | CONNECTS
  optional string protocol = 3;    // free-form; compared only if both ends set it
  PortAttachment attachment = 4;   // UNSPECIFIED is treated as SOCKET (DD-14)
  PortTransport transport = 5;     // UNSPECIFIED is treated as STREAM (DD-26)
}

enum PortDirection {
  PORT_DIRECTION_UNSPECIFIED = 0;
  PORT_DIRECTION_PROVIDES = 1;
  PORT_DIRECTION_REQUIRES = 2;
  PORT_DIRECTION_CONNECTS = 3;     // undirected; pairs with one other CONNECTS port (DD-27)
}

enum PortAttachment {
  PORT_ATTACHMENT_UNSPECIFIED = 0; // socket
  PORT_ATTACHMENT_SOCKET = 1;      // local socket; `transport` says what it carries
  PORT_ATTACHMENT_WIRED = 2;       // physical cable or bus; not forwardable
  PORT_ATTACHMENT_WIRELESS = 3;    // over-the-air medium; not forwardable
}

// What a SOCKET port carries. Forwards in this JEP accept STREAM only.
enum PortTransport {
  PORT_TRANSPORT_UNSPECIFIED = 0;  // stream
  PORT_TRANSPORT_STREAM = 1;       // TCP byte stream; forwardable
  PORT_TRANSPORT_DATAGRAM = 2;     // UDP, unicast or multicast; not forwardable
  PORT_TRANSPORT_FRAME = 3;        // link-layer frames; not forwardable
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

The client-facing `jumpstarter.client.v1.Exporter` gains
`repeated jumpstarter.v1.PortReport ports = 8` (output only), the union of
the ports in its driver reports, so `jmp get exporters --with ports` can
list them. That message carries no driver reports today, and its `status`
is the exporter status enum.

**`LeaseSpec`** gains three optional lists and a provenance reference:

```go
// Member and forward names are list-map keys, so the API server rejects
// duplicates itself, with no CEL cost. Every list is bounded, which keeps
// the remaining rules within the CEL cost budget.
// +kubebuilder:validation:XValidation:rule="!has(self.constraints) || (has(self.members) && self.constraints.all(c, c.members.all(n, self.members.exists(m, m.name == n))))",message="constraints must reference declared members"
// +kubebuilder:validation:XValidation:rule="!has(self.forwards) || (has(self.members) && self.forwards.all(f, f.endpoints.all(e, self.members.exists(m, m.name == e.member))))",message="forward endpoints must reference declared members"
type LeaseSpec struct {
    // ... all existing fields unchanged ...

    // Members of a member-form lease. When empty, the lease binds a single
    // exporter using the top-level Selector/ExporterRef exactly as before.
    // +listType=map
    // +listMapKey=name
    // +kubebuilder:validation:MaxItems=8
    Members []LeaseMember `json:"members,omitempty"`

    // Co-location constraints between members. Requires Members.
    // +kubebuilder:validation:MaxItems=4
    Constraints []LeaseConstraint `json:"constraints,omitempty"`

    // Port forwards between members. Requires Members.
    // +listType=map
    // +listMapKey=name
    // +kubebuilder:validation:MaxItems=16
    Forwards []LeaseForward `json:"forwards,omitempty"`

    // The template this lease was expanded from. Set by ClientService at
    // creation, immutable, and never read during binding (DD-19).
    TemplateRef *LeaseTemplateReference `json:"templateRef,omitempty"`
}

// The listed members must bind exporters that all carry SameLabel with one
// shared value. An omitted optional member is ignored (DD-22).
type LeaseConstraint struct {
    // +kubebuilder:validation:MinItems=2
    // +kubebuilder:validation:MaxItems=8
    Members []string `json:"members"`
    // A label key, for example "bus" or "rf-domain". The only rule in this
    // JEP; further rules join it as mutually exclusive fields.
    SameLabel string `json:"sameLabel"`
}

type LeaseTemplateReference struct {
    Name       string `json:"name"`
    // metadata.generation of the template at expansion.
    Generation int64  `json:"generation"`
}

// Exactly one non-empty selection source is required for every member.
// Optional fields can be absent, so every rule guards them with has().
// +kubebuilder:validation:XValidation:rule="!has(self.excluded) || !self.excluded || (has(self.optional) && self.optional)",message="only an optional member can be excluded"
// +kubebuilder:validation:XValidation:rule="(((has(self.selector) && ((has(self.selector.matchLabels) && size(self.selector.matchLabels) > 0) || (has(self.selector.matchExpressions) && size(self.selector.matchExpressions) > 0))) ? 1 : 0) + ((has(self.exporterRef) && has(self.exporterRef.name) && size(self.exporterRef.name) > 0) ? 1 : 0)) == 1",message="exactly one of selector or exporterRef.name is required"
type LeaseMember struct {
    // DNS-label syntax keeps names usable in the CLI and generated formats.
    // Python maps role names to LeaseMembers attributes, "-" becoming "_".
    // +kubebuilder:validation:MaxLength=63
    // +kubebuilder:validation:Pattern=`^[a-z0-9]([-a-z0-9]*[a-z0-9])?$`
    Name          string                       `json:"name"`
    Selector      metav1.LabelSelector         `json:"selector,omitempty"`
    ExporterRef   *corev1.LocalObjectReference `json:"exporterRef,omitempty"`
    Optional      bool                         `json:"optional,omitempty"`
    // Never bound; set by template expansion for --without (DD-21).
    Excluded      bool                         `json:"excluded,omitempty"`
    AllowDisabled bool                         `json:"allowDisabled,omitempty"`
}

type LeaseForward struct {
    Name string `json:"name"`

    // Endpoints on distinct members, in any order. At bind time exactly one
    // must resolve to `provides` and every other one to `requires` (DD-13,
    // DD-23), or exactly two must resolve to `connects` (DD-27).
    // Keyed by member, so two endpoints on one member are rejected.
    // +listType=map
    // +listMapKey=member
    // +kubebuilder:validation:MinItems=2
    // +kubebuilder:validation:MaxItems=8
    Endpoints []ForwardEndpoint `json:"endpoints"`

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
    // Pending | Bound | Omitted | Lost. Omitted: an optional member that did
    // not bind. Lost: a bound member whose exporter went away (DD-3).
    State       string                       `json:"state"`
    // Existing reason vocabulary (NoAccess, Offline, ...) for Omitted, Lost,
    // and a Pending member that blocks binding, plus Excluded (DD-21).
    Reason      string                       `json:"reason,omitempty"`
    Message     string                       `json:"message,omitempty"`
    ExporterRef *corev1.LocalObjectReference `json:"exporterRef,omitempty"`
    Priority    int                          `json:"priority,omitempty"`
    SpotAccess  bool                         `json:"spotAccess,omitempty"`
}

type LeaseForwardStatus struct {
    Name    string `json:"name"`
    State   string `json:"state"` // Pending|Disabled|Listening|Connected|Failed
    Mode    string `json:"mode,omitempty"`
    Message string `json:"message,omitempty"`
}
```

`ExporterStatus.Devices[]` gains the reported ports so the controller can
validate forwards against bound exporters. `ExporterStatus` itself gains
three optional fields used only to decide direct eligibility:

```go
    // Address peers in the same zone can dial for a direct forward, if this
    // exporter runs a peer listener. Never a device port (see Security).
    PeerEndpoint string `json:"peerEndpoint,omitempty"`
    // Expiry of the exporter's current identity certificate (DD-25), as
    // the exporter reports it, whichever issuer signed it.
    IdentityExpiresAt *metav1.Time `json:"identityExpiresAt,omitempty"`
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

**`ExporterClaim`** is a new namespaced resource that only the controller
writes (DD-28). Its name is derived from what it claims, so at most one
can exist for each exporter and each exclusion group:

```go
// Name: "exporter-" or "group-" followed by the first 32 hex digits of the
// SHA-256 of the exporter or group name. Carries a controller owner
// reference to the holding lease.
// +kubebuilder:validation:XValidation:rule="self == oldSelf",message="spec is immutable"
// +kubebuilder:validation:XValidation:rule="has(self.exporter) != has(self.exclusionGroup)",message="exactly one of exporter or exclusionGroup is required"
type ExporterClaimSpec struct {
    Exporter       string `json:"exporter,omitempty"`
    ExclusionGroup string `json:"exclusionGroup,omitempty"`
    // The lease that holds the claim.
    LeaseRef ClaimLeaseReference `json:"leaseRef"`
}

type ClaimLeaseReference struct {
    Name string    `json:"name"`
    UID  types.UID `json:"uid"`
}
```

**`LeaseTemplate`** is a new namespaced resource. Its `members`,
`constraints`, and `forwards` reuse the lease types and the same CEL rules,
so a template that validates expands into a lease that validates (DD-19):

```go
type LeaseTemplateSpec struct {
    Description  string   `json:"description,omitempty"`
    Docs         []string `json:"docs,omitempty"`         // URLs; never fetched
    Image        string   `json:"image,omitempty"`        // URL; never fetched
    Instructions string   `json:"instructions,omitempty"` // plain text

    // Same list-map keys and bounds as LeaseSpec.
    // +listType=map
    // +listMapKey=name
    // +kubebuilder:validation:MinItems=1
    // +kubebuilder:validation:MaxItems=8
    Members     []LeaseMember     `json:"members"`
    // +kubebuilder:validation:MaxItems=4
    Constraints []LeaseConstraint `json:"constraints,omitempty"`
    // +listType=map
    // +listMapKey=name
    // +kubebuilder:validation:MaxItems=16
    Forwards    []LeaseForward    `json:"forwards,omitempty"`

    Defaults LeaseTemplateDefaults `json:"defaults,omitempty"`
}

type LeaseTemplateDefaults struct {
    Duration *metav1.Duration `json:"duration,omitempty"`
}
```

The template has no status. `ClientService` computes availability when a
client lists templates, so templates generate no writes while exporters
come and go.

The existing CEL rules are extended, not replaced. The current top-level
"one of selector or exporterRef is required" rule gains a `members` arm and
mutual exclusion. Per-member CEL requires exactly one *non-empty* `selector`
or `exporterRef.name`; both set and both unset are rejected. Member and
forward names, and the members of a forward's endpoints, are list-map keys
(`+listType=map`), so the API server rejects duplicates without a CEL rule.
Additional rules enforce immutable member names, forwards and constraints
referencing declared members, immutability of `members`, `constraints`,
`forwards`, and `templateRef` (mirroring `tags` and `context`), two to eight
forward endpoints, and `excluded` only on optional members. Every rule
guards optional fields with `has()`, and every list it iterates is bounded
(eight members, four constraints, sixteen forwards, eight endpoints), so the
rules stay within the API server's CEL cost budget. Duplicate forward names
are thus rejected before the controller creates status maps.

**Protocol** — additions to existing messages, two client RPCs, and two
exporter RPCs. These are the wire definitions; the similarly named Go structs
above describe the CRD only.

Clients create and read leases through `jumpstarter.client.v1.ClientService`
(`CreateLease`, `GetLease`, `ListLeases`), and that is where member-form
leases and templates are added. The older lease RPCs on
`jumpstarter.v1.ControllerService` (`RequestLease`, `GetLease`,
`ListLeases`) stay scalar-only: `GetLeaseResponse.exporter_uuid` is unset
for a member-form lease, as it is for an unbound one. `Dial`, `Listen`, and
the new `DialPeer` and `SignExporterCertificate` stay on `ControllerService`.

```protobuf
// jumpstarter/v1 — shared with exporters
enum LeaseForwardMode {
  LEASE_FORWARD_MODE_UNSPECIFIED = 0; // Auto
  LEASE_FORWARD_MODE_AUTO = 1;
  LEASE_FORWARD_MODE_ROUTER = 2;
  LEASE_FORWARD_MODE_DIRECT = 3;
}

enum ForwardSide {
  FORWARD_SIDE_UNSPECIFIED = 0;
  FORWARD_SIDE_PROVIDES = 1;
  FORWARD_SIDE_REQUIRES = 2;
  FORWARD_SIDE_CONNECTS = 3;       // one end of an undirected path (DD-27)
}
```

```protobuf
// jumpstarter/client/v1
enum LeaseForwardState {
  LEASE_FORWARD_STATE_UNSPECIFIED = 0;
  LEASE_FORWARD_STATE_PENDING = 1;
  LEASE_FORWARD_STATE_DISABLED = 2;
  LEASE_FORWARD_STATE_LISTENING = 3; // Set up; no live connection.
  LEASE_FORWARD_STATE_CONNECTED = 4; // At least one live connection.
  LEASE_FORWARD_STATE_FAILED = 5;
}

enum LeaseMemberState {
  LEASE_MEMBER_STATE_UNSPECIFIED = 0;
  LEASE_MEMBER_STATE_PENDING = 1;
  LEASE_MEMBER_STATE_BOUND = 2;
  LEASE_MEMBER_STATE_OMITTED = 3;
  LEASE_MEMBER_STATE_LOST = 4;
}

message LeaseMember {
  string name = 1;
  oneof selection {
    string selector = 2;      // Label selector expression, as in Lease.selector.
    string exporter_name = 3;
  }
  bool optional = 4;
  bool allow_disabled = 5;
  bool excluded = 6;         // Set by template expansion; never bound.
}

message LeaseConstraint {
  repeated string member_names = 1;
  oneof rule {
    string same_label = 2;    // The only rule in this JEP (DD-22).
  }
}

message ForwardEndpoint {
  string member_name = 1;
  string port_name = 2; // Unique within the member exporter.
}

message LeaseForward {
  string name = 1;
  // Two to eight, on distinct members, in any order. Exactly one must
  // resolve to PROVIDES and the rest to REQUIRES (DD-13, DD-23), or
  // exactly two must resolve to CONNECTS (DD-27).
  repeated ForwardEndpoint endpoints = 2;
  jumpstarter.v1.LeaseForwardMode mode = 3;
}

// Request-time use of a template (DD-21). On read, only `template` and
// `generation` are set, from `spec.templateRef`: expansion folds the request
// fields into `members`, where they can be read.
message LeaseTemplateUse {
  string template = 1 [(google.api.resource_reference) = {type: "jumpstarter.dev/LeaseTemplate"}];
  // Optional roles only; sets LeaseMember.excluded.
  repeated string excluded_members = 2 [(google.api.field_behavior) = INPUT_ONLY];
  // Role to selector expression, ANDed with the template's.
  map<string, string> member_selectors = 3 [(google.api.field_behavior) = INPUT_ONLY];
  int64 generation = 4 [(google.api.field_behavior) = OUTPUT_ONLY];
}

message LeaseMemberStatus {
  string name = 1;
  LeaseMemberState state = 2;
  optional string exporter = 3 [(google.api.resource_reference) = {type: "jumpstarter.dev/Exporter"}];
  optional string reason = 4;
  optional string message = 5;
  int32 priority = 6;
  bool spot_access = 7;
}

message LeaseForwardStatus {
  string name = 1;
  LeaseForwardState state = 2;
  jumpstarter.v1.LeaseForwardMode mode = 3; // Transport of the most recent connection.
  optional string message = 4;
}

message Lease {
  // ... fields 1-18 unchanged (17 and 18 are lease sharing's `shared_with`
  // and `effective_shared_with`); `selector` is required only for the
  // scalar form, and `exporter` is set only for scalar leases ...
  repeated LeaseMember members = 19 [(google.api.field_behavior) = IMMUTABLE];
  repeated LeaseConstraint constraints = 20 [(google.api.field_behavior) = IMMUTABLE];
  repeated LeaseForward forwards = 21 [(google.api.field_behavior) = IMMUTABLE];
  optional LeaseTemplateUse template = 22 [(google.api.field_behavior) = IMMUTABLE];
  repeated LeaseMemberStatus member_statuses = 23 [(google.api.field_behavior) = OUTPUT_ONLY];
  repeated LeaseForwardStatus forward_statuses = 24 [(google.api.field_behavior) = OUTPUT_ONLY];
}

message LeaseTemplateMember {
  LeaseMember member = 1;
  int32 available = 2 [(google.api.field_behavior) = OUTPUT_ONLY]; // Leasable candidates now.
}

message LeaseTemplate {
  option (google.api.resource) = {
    type: "jumpstarter.dev/LeaseTemplate"
    pattern: "namespaces/{namespace}/leasetemplates/{leasetemplate}"
    singular: "leaseTemplate"
    plural: "leaseTemplates"
  };
  string name = 1 [(google.api.field_behavior) = IDENTIFIER];
  string description = 2;
  repeated string docs = 3;
  optional string image = 4;
  optional string instructions = 5;
  repeated LeaseTemplateMember members = 6;
  repeated LeaseConstraint constraints = 7;
  repeated LeaseForward forwards = 8;
  optional google.protobuf.Duration default_duration = 9;
  int64 generation = 10;
}

service ClientService {
  // ... existing RPCs unchanged ...
  rpc GetLeaseTemplate(GetLeaseTemplateRequest) returns (LeaseTemplate);
  rpc ListLeaseTemplates(ListLeaseTemplatesRequest) returns (ListLeaseTemplatesResponse);
}
```

`CreateLease` checks the request form before it creates the lease, so a
malformed request never reaches exporter selection. Unless the request uses
exactly one of these forms, it returns `INVALID_ARGUMENT` naming the
conflicting fields, and creates nothing:

- **Scalar:** `selector`, `exporter_name`, or both, as today, with no
  `members`, `constraints`, `forwards`, or `template`.
- **Template:** `template`, optionally with `excluded_members` and
  `member_selectors`, with no scalar field and no `members`, `constraints`,
  or `forwards`. The controller then expands the template as described in
  *Template expansion*.
- **Members:** `members`, optionally with `constraints` and `forwards`, with
  no scalar field and no `template`.

So `constraints` or `forwards` without `members`, and a scalar field
together with `members` or `template`, are rejected before any lease or
claim exists. CEL at admission enforces the same mutual exclusion on the
stored `Lease`, for requests that do not come through `ClientService`.
List and get requests follow the existing paging and `parent` conventions. There is no
create, update, or delete RPC for templates.

```protobuf
// jumpstarter/v1 — exporter-facing
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
  oneof forward {                            // NEW; unset for a client connection
    ForwardSetup forward_setup = 3;          // once per forward
    ForwardTeardown forward_teardown = 4;
    ForwardConnect forward_connect = 5;      // once per connection, PROVIDES side
  }
}

message ForwardPeer {
  string member_name = 1;
  string port_name = 2;
  string identity = 3;  // Identity URI of the peer's exporter (DD-25).
}

message ForwardSetup {
  string lease_name = 1;
  string lease_uid = 2;
  string forward_name = 3;
  string member_name = 4;
  // The providing endpoint for REQUIRES; every bound requiring endpoint
  // for PROVIDES; the other end for CONNECTS.
  repeated ForwardPeer peers = 5;
  ForwardSide side = 6;
  string local_driver_uuid = 7; // Resolved from the exporter-wide unique port.
  string local_port_name = 8;
  LeaseForwardMode mode = 9;
  bool direct_eligible = 10;    // Use the exporter identity certificate (DD-25).
  bool initiator = 11;          // CONNECTS only: this end opens the path (DD-27).
}

message ForwardTeardown {
  string lease_uid = 1;
  string forward_name = 2;
}

// Tells the PROVIDES side to open one connection. It mirrors what Dial sends
// an exporter for a client connection.
message ForwardConnect {
  string lease_uid = 1;
  string forward_name = 2;
  string connection_id = 3;     // The stream ID; also names a direct arrival.
  string peer_member_name = 4;  // The requiring member opening the connection.
  string peer_identity = 5;     // Identity its certificate must carry (DD-25).
  string router_endpoint = 6;   // Empty in Direct mode.
  string router_token = 7;      // The token DialPeer returned.
}

service ControllerService {
  // ... existing RPCs unchanged ...
  rpc DialPeer(DialPeerRequest) returns (DialPeerResponse);
  rpc SignExporterCertificate(SignExporterCertificateRequest)
      returns (SignExporterCertificateResponse);
}

// Called by the REQUIRES side for each connection its listener accepts.
message DialPeerRequest {
  string lease_name = 1;
  string forward_name = 2;
  string member_name = 3;
}

message DialPeerResponse {
  string router_endpoint = 1;         // Empty in Direct mode.
  string router_token = 2;            // `sub` is the new stream ID, as in Dial.
  string connection_id = 3;           // That stream ID.
  optional string peer_endpoint = 4;  // Set when a direct dial is tried first.
  optional string peer_identity = 5;  // Identity the listener must present.
}

// An exporter sends a CSR for a key it generated; the key never leaves it.
// Authenticated with the exporter's existing credential (DD-25).
message SignExporterCertificateRequest {
  bytes certificate_signing_request = 1;  // PKCS#10, DER.
}

// Field encodings follow the SPIFFE Workload API's X509SVID message.
message SignExporterCertificateResponse {
  string spiffe_id = 1;          // The identity the certificate carries.
  bytes certificate_chain = 2;   // DER certificates, leaf first; MAY include intermediates.
  // DER CA certificates, concatenated, keyed by trust domain SPIFFE ID
  // ("spiffe://<trust-domain>"). This JEP fills one entry, its own.
  map<string, bytes> trust_bundles = 3;
  google.protobuf.Timestamp expiry = 4;
}
```

`DialPeer` mirrors `Dial`. The controller checks that the caller is the
exporter bound to `member_name` and that the member is a requiring endpoint
of the forward, or the initiator of an undirected path. It creates a stream
ID and signs one router token whose `sub` is that ID. It returns the token
and sends the same token in `ForwardConnect` to the providing exporter, or
to the other end of the path. If the providing exporter has no `Listen`
stream, `DialPeer`
fails with `UNAVAILABLE` and the `requires` side closes the local
connection.

Direct connections carry no bearer secret. Each exporter holds an identity
certificate for a key it generated, signed through
`SignExporterCertificate` (DD-25). The peer listener and the dialer both
present it. The providing exporter accepts a direct arrival only if the
connection ID names a pending `ForwardConnect` and the dialer's certificate
carries that instruction's `peer_identity`, and it accepts each connection
ID once.

The peer listener is a gRPC server that implements `RouterService.Stream`
and nothing else. It serves only the one pair it was announced. The dialer
sends the connection ID in the `jumpstarter-connection-id` request metadata
where a router call carries its bearer token, and the listener splices the
stream to its local service. The `requires` side therefore makes the same
call whether it reaches the router or its peer.

For an ordinary client connection, fields 1 and 2 are both populated and
the `forward` oneof is unset. For setup, teardown, or connect, only the corresponding
oneof member is populated and fields 1 and 2 are empty; an exporter rejects
a response that combines them. An old exporter reports no ports, so it cannot
be selected for a forward and never receives a forward instruction. A new
exporter checks the `forward` oneof before treating fields 1 and 2 as a
client connection. Unknown fields remain safe under proto3.
`ReleaseLeaseRequest` is untouched. `RouterService` is unchanged: its
protobuf, token validation, and pairing stay as they are (DD-5).

**CLI surface** — existing commands with new flags and subcommands:

- `jmp create lease` and `jmp shell` take `-t`/`--lease-template NAME`,
  with `--without ROLE` and `--member ROLE=SELECTOR` to narrow it (DD-21)
- `jmp create lease` and `jmp shell` take ad-hoc
  `--member role=selector`, `--forward name=m.port,m.port`, and
  `--same-label key=role,role` (endpoint order is irrelevant; direction is
  resolved at bind time). A `--forward` with more than two endpoints fans
  one provided port out to several required ports (DD-23). Ad-hoc members
  exist only in the CLI (DD-20).
- `jmp get lease-templates` and `jmp describe lease-template NAME`, new
  subcommands of the existing `get` and `describe` groups
- `jmp get leases` prints per-role exporters and the template;
  `-o json|yaml|name` unchanged
- `jmp get leases <name> -o mobly` writes a testbed file for Mobly, Google's
  multi-device test framework (see *References*)
- `jmp get exporters --with ports` shows declared ports, beside the existing
  `leases`, `online`, and `status` values
- `j <role> <driver> ...` and the new `j-forward status` command inside
  `jmp shell` (DD-24)
- `jmp admin apply -f FILE` accepts `LeaseTemplate` manifests beside the
  kinds it applies today; `kubectl apply` works as well
- `jmp delete lease` / `jmp update lease` need no changes

### Hardware Considerations

- **Hardware:** virtual devices need KVM-capable hosts but no physical
  radios. Physical devices use existing harnesses. Leases that mix a
  virtual device with a physical radio are not supported (DD-11).
- **RF range and isolation:** physical radio peers must be within range.
  Shared labs may need shielded enclosures or channel planning. Exporter
  labels such as `rf-domain: rack-3`, with a `sameLabel` constraint, keep
  members within range (DD-22); the controller does not measure RF
  interference.
- **Bluetooth latency:** HCI flow control and audio buffering can be more
  sensitive than supervision timeouts. Measure both router and direct paths
  against the intended workloads (DD-4).
- **Wi-Fi simulation:** vhost-user needs a frame bridge, and TCP adds
  head-of-line blocking that may affect medium timing (DD-10).
- **Projection receiver:** virtual head units use a software receiver, such as
  Google's Desktop Head Unit, run by the `projection-rx` driver (see
  *Reference drivers for projection*).
- **Listener isolation:** fixed `requires` addresses rely on each exporter
  owning its network namespace. Multiple host-networked exporters sharing
  one machine are unsupported. `requires` listeners bind without
  `SO_REUSEPORT` (`TemporaryTcpListener(reuse_port=False)`; the default is
  `True`), so an address already in use fails instead of being shared
  silently.
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
Writing all members in one `Status().Update`, after taking their claims
(DD-28), prevents a lease from persisting a partial acquisition.

Child leases would bind independently and require acquisition timeouts,
release-and-retry behavior, and contention handling. Client-side coordination
has the same partial-acquisition problem. Both approaches also need a way to
select a member when dialing.

The status write alone does **not** make claims exclusive across leases.
Lease reconciles run one at a time, but each reads the active leases from
the controller's cache, which can lag the status write of the lease
reconciled just before. On `main`, two leases can therefore claim the same
exporter, and nothing detects or repairs it: once `status.exporterRef` is
set the controller does not re-evaluate it, and the exporter's
`status.leaseRef` follows whichever lease is listed last. Leases with more
members would hit this more often. DD-28 closes it for scalar and
member-form leases alike.

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
consumers such as the Host Orchestrator façade from the Cuttlefish
orchestration draft (*References*). Replacing it with a
list would require every consumer to migrate.

`Lease.exporter` in `ClientService` and the legacy
`GetLeaseResponse.exporter_uuid` follow the same convention. A scalar-form
lease returns a bare driver client. An explicit one-member or multi-member
lease returns a `LeaseMembers` instance with one attribute per role. The request
form, rather than the number of bound exporters, therefore determines a
stable response shape. A template always expands to the member form, even
with one member, so a role name such as `headunit` stays valid if the
template later gains a member.

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
artifacts before release. The lease reports `Ready=False` and `Degraded`,
the failed role's `status.members[].state` becomes `Lost` with a reason, and
the client raises on the next call into that role.
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

### DD-5: One router stream per forwarded connection; no router change

**Alternatives considered:**

1. **A stream ID per connection, issued by the controller.** The `requires`
   side asks the controller for each connection it accepts. The controller
   creates a stream ID and gives both exporters a token for it, as `Dial`
   does for a client connection.
2. **One stable subject per forward**, derived from the lease UID and forward
   name, with member, exporter, and side claims that the router validates.
3. **A peer-specific RPC** in `router.proto` with explicit A/B roles.

**Decision:** Option 1. `RouterService` is unchanged.

**Rationale:** The router pairs the two callers whose tokens carry the same
`sub` and checks nothing else about them. For a client connection, `Dial`
creates a random stream ID, signs one token with it, and hands that token to
both the client and, over `Listen`, the exporter. Option 1 uses the same
mechanism with an exporter in the client's place: `DialPeer` authenticates
the calling exporter against the lease, and `ForwardConnect` delivers the
token to the other exporter on its authenticated `Listen` stream. Only those
two exporters hold the token, so the router needs no new claims or checks,
and its code and protobuf stay as they are.

A forward can carry many connections, and some protocols open several at
once. With one subject per forward (option 2), the router cannot tell two
concurrent connections apart, the `provides` side has no signal to open a
stream, and the token must stay valid for the whole lease. A stream ID per
connection keeps each splice separate, tells the `provides` side when to
dial its service, and lets tokens be as short-lived as `Dial`'s. It also
lets one provided port serve several members (DD-23). Option 3 would
duplicate the stream path without adding a check the controller does not
already make.

The cost is one controller round trip per connection. New connections need
the controller to be reachable, as client connections already do;
established connections do not.

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

A third direction, `connects`, describes a port with no provider or
requirer. Two `connects` ports form one bidirectional path (DD-27): two
serial bridges joined as a cross-over, for example. Several of them could
later form a shared link, such as an automotive Ethernet segment where
ECUs find each other through SOME/IP service discovery (DD-26). A
`connects` port pairs only with another `connects` port, so declaring it
is how a driver opts in. Joining two `provides` or two `requires` ports
stays invalid, because for those the direction is a real constraint.

Each direction has one spelling. Synonyms such as `tx`/`rx` or
`sender`/`receiver` would make reports and templates harder to compare, and
they suggest one-way data, while every forward is a bidirectional stream.

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
  its controller stream but the stand-in forward stayed down. A cut stream
  must close its local connection, the endpoint's listener must stay open
  so the next connection gets a new stream, and drivers that need to
  restore protocol state must be notified (*Forward validation and
  establishment*).
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
(`projection-wifi`). The template selects the path (DD-13, DD-20).

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
lease request, whether the lease came from a template or the CLI. A
template describes wiring; it grants no access. Lease priority is the minimum member priority, and duration
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

1. **Infer direction only.** A forward lists its endpoints in any order;
   the controller decides which one provides and which ones require from
   the reported ports. Which ports are joined stays explicit.
2. **Infer topology too** — auto-forward every `provides`/`requires` pair the
   bound exporters happen to expose, with no `forwards` stanza at all.
3. **Infer nothing** — the author marks the source and destinations of
   every forward, alone or as a second form beside option 1.

**Decision:** Option 1.

**Rationale:** Port direction is already in the exporter report. The
controller can resolve it without requiring the lease author to repeat it.
The template author must still choose which ports to connect: forwarding
`projection` and forwarding `projection-wifi` exercise different paths.

Automatic topology would make connections depend on the ports exposed by
whichever exporters were selected. Multiple possible matches would be
ambiguous and could create data paths the test did not request.

A port's direction is fixed by its driver, so stating it adds only a check
that the report already makes, and a second form would give every consumer
of the API two shapes to handle. With one list, the CLI flag, the template,
the stored lease, and the protobuf all use the same representation.
Templates and CLI flags both produce explicit `spec.forwards[]` entries, so
the stored topology remains inspectable.

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

Software state on an exporter can be selected through dynamic labels (the
Dynamic Exporter Labels draft; see *References*).

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

A lease holds a claim on every group its exporters belong to, beside the
claims on the exporters themselves, so the API server makes groups
exclusive in the same way (DD-28).

Exclusion groups are the opposite of shared infrastructure (DD-14). A group
is several exporters for one DUT and one lease. A shared infrastructure
exporter is one exporter serving many DUTs and leases.

### DD-17: Type multi-exporter leases as classes bound to a template

**Alternatives considered:**

1. **A `LeaseMembers` subclass bound to a template.** Members are annotated
   class attributes, and forwards are attributes annotated `Forward`. The
   class names its template; selectors and endpoints stay in the template.
   `config.lease(Members)` yields that class from `connect()`.
2. **A self-contained `LeaseMembers` subclass.** The class declares each
   member's selector with `member(selector=...)` and each forward's
   endpoints with `forward(a, b)`, and is itself the lease request.
3. **Keyword dictionaries.** `config.lease(members={...}, forwards=[...])`
   and a `members["role"]` mapping.
4. **Dynamic attributes.** Roles installed on the connected object at
   connect time.

**Decision:** Option 1.

**Rationale:** Driver clients are already typed classes, such as
`PowerClient` and `AdbClient`, but a lease's `connect()` yields an untyped
tree whose children are found by attribute lookup at runtime. Option 3
keeps that: a misspelled role or driver fails only when the test reaches
that line. With a class, a type checker resolves `members.phone.adb` to
`AdbClient` and reports an unknown role, a missing driver, or a wrong call
before the lease is requested.

Options 2 and 3 put selectors and port names in test code, which DD-20
excludes. Option 1 splits the two concerns: the template says which
exporters and how they are wired, and the class says what the test expects
to find on them. The client compares the two before requesting, so a class
that no longer matches its template fails with the role or forward named,
not with a lease that binds and then breaks. `connect()` checks that each
bound exporter provides the drivers the member's class declares, so a
misconfigured exporter fails with the role and driver named rather than as
an `AttributeError` later in the test.

Option 4 gives no static types and lets a role shadow client attributes.
Class attributes cannot collide that way: `by_role` and `metadata` are the
only reserved names. Roles that are not valid identifiers map from underscores, as in
`head_unit` for `head-unit`, or use `member(role=...)`.

Each member is typed as a `CompositeClient` subclass, the same base that
exporter client trees and hand-written composite clients such as
`QemuClient` already use. Its annotations give the type checker the
children that `CompositeClient.__getattr__` resolves at runtime. It is also
the shape a code generator would emit, one subclass per member and one
annotation per driver, once ExporterClass gives generators a
language-neutral description of each member's drivers. Hand-written and
generated member types would then have one shape, and a test could move
from one to the other without changing call sites. A `Protocol` would describe the same
shape but could not be instantiated, so `connect()` would return the
untyped tree and the checker's view would diverge from the runtime object.

The class holds only names and types: the template name, roles, driver
names mapped to client classes, and forward names. Member classes add
annotations only; methods belong on driver clients. The template already
holds a language-neutral form of the roles and forwards. Driver types get
one when members can name an `ExporterClass`, and a code generator can then
produce the same typed lease in other languages (see *Future
Possibilities*). This JEP defines no interface identifiers of its own.

### DD-18: Assign members by augmenting paths, not first fit

**Alternatives considered:**

1. **First fit.** Each member, in order, takes its best remaining candidate.
2. **First fit, fewest candidates first.** Sort members by candidate count,
   then apply first fit.
3. **Augmenting paths.** Each member takes its best free candidate; if none
   is free, earlier members move along the shortest chain of alternative
   candidates to free one (bipartite matching).
4. **Minimum-cost assignment** (Hungarian algorithm) over candidate rank or
   policy priority.

**Decision:** Option 3.

**Rationale:** First fit can fail a lease that has a valid assignment. If
member A qualifies for exporters `x` and `y` and member B only for `x`, A
takes `x` and B is never bound. Candidate order is total and deterministic,
so every requeue repeats the choice: the lease waits indefinitely while both
exporters sit idle. An optional member listed before a required one can
starve it the same way. Sorting by candidate count makes this rarer but not
impossible. Augmenting paths bind whenever any assignment exists, keep first
fit's choice whenever first fit would bind every member, and leave a search
tree on failure that names the roles competing for too few exporters.
Minimum-cost assignment would also optimize rank, but it changes picks in
uncontended cases and is harder to explain from a status message. A lease has
at most eight members, so the cost is a few small breadth-first searches per
reconcile.

The same search handles constraints: it runs once per combination of
constraint values (DD-22). Without constraints there is one combination.

### DD-19: Reusable definitions as a `LeaseTemplate` expanded at creation

**Alternatives considered:**

1. **A namespaced `LeaseTemplate` resource, expanded at creation.** The
   controller copies the template into the new lease, which records the
   template name and generation as provenance.
2. **A live reference.** The lease stores only `templateRef`, and binding
   reads the template on every reconcile.
3. **A client-side file.** A language-neutral file in the test repository
   that the client expands before calling `CreateLease`.

**Decision:** Option 1.

**Rationale:** The template belongs to the lab, so it lives in the lab's
namespace beside the exporters it describes, and the people who wire those
exporters update it. A file in test repositories (option 3) would be copied
into each one and go stale when the lab changes. Clients have no
Kubernetes access, so they read templates through `ClientService`.

Expanding at creation keeps the scheduler unchanged: binding, member claims,
readiness, and forwards see an ordinary member-form lease and never read
`templateRef`. It also keeps a lease's meaning fixed. With a live reference
(option 2), editing a template would change running leases, contradicting
the immutability of `members` and `forwards`, and deleting a template would
strand them. Under option 1 an edit affects only later requests, and
`templateRef.generation` shows which version a lease used.

The name `LeaseTemplate` says what it is: something a lease is made from.
`Testbed` is already used for the Mobly export, and `Bench` suggests one
physical workbench rather than a definition that matches any suitable set
of exporters.

Degradation and missing members are reported where every member-form
lease reports them: conditions and `status.members[].state`. The only
template-specific status is provenance in `spec.templateRef`; roles the
request excluded are members marked `excluded`. A separate template status
would duplicate that.

### DD-20: Templates and the CLI are the only sources of member-form leases

**Alternatives considered:**

1. **Templates from any client, ad-hoc members from the CLI only.**
2. **Ad-hoc members from any client**, with templates as one more option.
3. **Templates only**, with no ad-hoc member-form leases at all.

**Decision:** Option 1.

**Rationale:** Selectors, port names, and constraints describe a lab, not
a test. Writing them into test code ties the test to one lab's labels and
breaks it when the lab is rewired (*Motivation*). With option 1 a test names
a template and the roles it uses, and each lab publishes a template with
that name for its own exporters. The definitions a lab supports are listed
in one place and reviewed by the people who maintain the hardware.

The CLI keeps an ad-hoc path because some work has no template yet:
bringing up a new pairing, debugging one exporter against another, or
checking a template before publishing it. Option 3 would make that work
wait for an administrator. That work is interactive and short-lived;
anything a test or pipeline depends on belongs in a template.

Both paths call the same `CreateLease` with the same fields, so the
controller cannot tell the CLI from a library. The boundary is the client
API: `jumpstarter` exposes templates and attaching to an existing lease,
and the CLI builds ad-hoc requests through internal helpers. A test that
needs an ad-hoc lease runs inside `jmp shell` or attaches by lease name.
Restricting ad-hoc requests at the server, for example to some clients, is
left to access policy (*Unresolved Questions*).

### DD-21: Requests narrow a template; they never extend it

**Alternatives considered:**

1. **Narrow only.** A request may AND further labels into a role's
   selector, exclude optional roles, and set duration, begin and end time,
   tags, and context. It cannot add roles, forwards, or constraints, or
   replace a selector.
2. **Free override.** A request may change any field of the expanded lease.
3. **No overrides.** A request names the template and a duration only.

**Decision:** Option 1.

**Rationale:** The template records how exporters are wired. Narrowing
cannot break that wiring: an exporter that matches the narrowed selector
still matches the template's, so every forward and constraint still holds.
Excluding an optional role only does early what binding does when that
role finds no exporter: the member stays in the lease, unbound, and its
forwards and constraints behave exactly as they would for an omitted
member. Expansion therefore never rewrites forwards or constraints. Option 2 would let a request turn a template into an
arbitrary lease, so the template no longer described what was leased.
Option 3 would force a separate template for every SoC or software variant
that one test wants to require.

`-l`/`--selector` and `-n`/`--name` apply to a scalar lease and name no role, so
they are rejected with a template. A request cannot pin a role to a named
exporter; that belongs in the template's `exporterRef` or in an ad-hoc CLI
lease.

### DD-22: Co-location by shared label value, chosen during binding

**Alternatives considered:**

1. **`sameLabel` constraints in the lease.** A constraint lists members and
   a label key; the bound exporters must share one value of it. The
   controller picks the value during binding.
2. **Pin the value in selectors.** Each member names `rf-domain: rack-3`.
3. **A constraint expression language** over member labels.
4. **A topology model** of buses, racks, and RF domains.

**Decision:** Option 1, in a `oneof` that later rules can join.

**Rationale:** Which bus or rack is free is known only at binding, so a
template cannot choose it at creation time. Option 2 limits a request to one
bench, and the lease waits while an identical bench sits idle. Label
equality covers the cases this JEP motivates (a shared CAN bus, one RF
domain, one harness) with data labs already keep as exporter labels. An
expression language (option 3) and a topology model (option 4) are larger
designs that need operational experience first. The `oneof` in
`LeaseConstraint` leaves room for them.

Binding tries each combination of values, then runs the DD-18 assignment
with each constrained member's candidates restricted to exporters carrying
that value. Values come only from candidates at the first filter stage, a
value is skipped unless every required member of its constraint has a
candidate with it, and combinations are tried in sorted order so the result
is deterministic. Constraints that share a member and a label key are
merged into one. The first combination that assigns every required member
binds. Four constraints over at most eight members keep this small in
practice. The number of combinations is fixed at the first filter stage,
because later stages only remove candidates. If it exceeds 1,024, the
controller searches nothing and reports `Unsatisfiable` with reason
`ConstraintLimit`, so a lab with many label values cannot stall the
reconciler. This ends the lease, like any `Unsatisfiable` result; a narrower
selector or a request-time narrowing (DD-21) reduces the count.

An exporter without the label cannot satisfy a constraint that names its
member. An optional member that does not bind is ignored by its
constraints, but one that binds must satisfy them.

### DD-23: One provided port can serve several required ports

**Alternatives considered:**

1. **Independent connections to one provided port.** A forward may list
   several required ports beside its provided port, and a provided port
   may appear in several forwards. Each required port opens its own
   connections to the service.
2. **Exactly two endpoints per port.** A port appears in one forward.
3. **A broadcasting forward** that copies the source's bytes to every
   destination.

**Decision:** Option 1.

**Rationale:** Many provided services already accept several clients: a
rootcanal HCI port takes several hosts, and a serial or CAN bridge takes
several readers. Because each connection has its own stream (DD-5), serving
another requiring port needs no new transport. It is one more listener and one
more `DialPeer` caller. Option 2 would make a lab run one bridge per
consumer. Option 3 puts protocol behavior in the forward: what a second
writer means, and what a late joiner has missed, depend on the service. The
service therefore decides whether its clients share data, and the forward
copies nothing between requiring ports.

A two-endpoint forward is the common case of the same list, so fan-out adds
no second shape. Listing several required ports in one forward and naming
one provided port in several forwards differ only in status: one forward
has one status entry, and separate forwards have one each. A `requires`
port has one listener and one upstream, so it can appear in only one
forward.

### DD-24: Shell commands and metadata for member-form leases

**Alternatives considered:**

1. **Roles under `j`, forwards under `j-forward`, members in one variable.**
   `j <role> ...` reaches a member's drivers, a separate `j-forward` command
   reports forwards, and `JMP_LEASE_MEMBERS` describes every member.
2. **`j forward` with reserved role names.** Forwards are a subcommand of
   `j`, and `forward` and `forwards` are rejected as role names.
3. **Reuse the single-exporter variables** by pointing `JUMPSTARTER_HOST`,
   `JMP_EXPORTER`, and `JMP_EXPORTER_LABELS` at the first member.

**Decision:** Option 1.

**Rationale:** The names under `j` come from exporters and templates: driver
names in a scalar shell, role names in a member-form one. A built-in
subcommand in that namespace needs reserved words in the API (option 2), and
it would still collide with a driver named `forward` if forwards later apply
to other lease forms. A separate command cannot collide, so role names need
no reserved words.

`jmp shell` describes its exporter to child processes through
`JMP_EXPORTER` and `JMP_EXPORTER_LABELS`, which `env_with_metadata()`
returns as `ExporterMetadata`. Tests use it to record the device they ran
on. A member-form lease has one such record per role, so the shell sets
`JMP_LEASE_MEMBERS`, and `LeaseMembers.metadata` exposes the same records
for a lease acquired in Python. Option 3 would hand existing scripts an
arbitrary member, which is the misrouting DD-2 avoids. Leaving the
single-exporter variables unset makes those scripts fail with a clear
message instead.

### DD-25: Exporter identity certificates for the direct path

This decision covers one kind of connection only: the direct peer-to-peer
connections between exporters, in Pods or on hosts, that carry a lease's
forwards. These forwards are the inter-exporter bench networking that a
`LeaseTemplate` declares, or that the CLI declares for an ad-hoc lease
(DD-20). Connections between clients, exporters, the controller, and the
router keep their current authentication.

**Alternatives considered:**

1. **An identity certificate per exporter, from a CSR.** Each exporter
   generates its own key and sends a certificate signing request to the
   controller, which signs a short-lived certificate naming that `Exporter`.
   Direct connections authenticate with these certificates over mTLS, and
   the lease decides which connections are allowed.
2. **Per-forward certificates issued by the controller.** For each forward,
   the controller generates a key pair, signs a certificate, and returns both
   to each endpoint, which also sends a bearer token inside the channel.
3. **Kubernetes Pod certificates only.** Exporter Pods mount an identity
   certificate that the kubelet requests from a Jumpstarter signer.
4. **TLS plus a bearer token.** The peer listener has a server certificate,
   and the dialer proves itself with a controller-issued token.

**Decision:** Option 1, used only for direct connections that carry a
lease's forwards. The certificate is the exporter's identity, not a forward
credential, so a later JEP on mTLS for the controller, router, and clients
can reuse it. Pod certificates (option 3) are a way to deliver the same
identity, not a different one.

**Rationale:** Jumpstarter authenticates exporters and clients with bearer
JWTs today, and the controller and router serve TLS without checking client
certificates. A bearer token protects nothing once it leaks, while a
certificate proves possession of a key that never leaves its holder. The
direct path is the first connection where neither end is the controller, so
it is where a mutual identity is needed first. It should be the identity
that the rest of the system can adopt, rather than one built only for
forwards.

Option 2 sends private keys over the network and ties each certificate to
one forward, so nothing else can reuse it. Option 4 keeps a bearer secret on
the path and still needs a server certificate per exporter. Option 3 alone
leaves out physical and non-Kubernetes exporters. Option 1 works wherever an
exporter can reach the controller, because the exporter's existing
credential authenticates the CSR.

Authentication and authorization stay separate. The certificate says which
exporter is on the other end; it grants nothing. The providing side admits
a direct connection only if the controller announced it in
`ForwardConnect`, over the providing exporter's own authenticated `Listen`
stream, and the dialer's certificate carries the identity named there. The
connection ID is not a secret: it only says which announced connection this
is. Since no bearer secret is left on the path, intercepting the connection
ID gains nothing.

The certificates are SPIFFE X.509-SVIDs. This JEP does not adopt SPIFFE
infrastructure such as the Workload API, SPIRE, or federation. It avoids
anything that would stop a later JEP from adopting it, or from accepting
identities issued by SPIFFE-compatible systems such as SPIRE or Teleport
Workload Identity. The design points:

- **Identity format.** The identity is a SPIFFE ID. For a certificate the
  controller signs, it is
  `spiffe://<trust-domain>/ns/<namespace>/exporter/<name>`. Kubernetes
  namespace and object names use only characters that SPIFFE allows in a
  path segment, and stay well under its 2,048-byte limit. The trust domain
  is controller configuration, never a built-in constant, and must be a
  valid SPIFFE trust domain name (lowercase `[a-z0-9.-_]`). The controller
  sets the identity from the authenticated `Exporter` and ignores any
  identity requested in the CSR. A client identity could later use
  `.../client/<name>` in the same form.
- **Identities are opaque to peers.** A peer compares the whole SPIFFE ID
  in the certificate with the one the controller named in `ForwardSetup`
  or `ForwardConnect`, and never derives a namespace or `Exporter` from the
  path. Only the controller maps exporters to identities. SPIRE and
  Teleport let operators choose ID paths, so a later JEP can map an
  `Exporter` to an ID issued elsewhere without changing the direct path.
- **Certificate profile.** Controller-signed certificates follow the
  X.509-SVID specification. The SPIFFE ID is the only URI SAN and is not
  repeated in the subject or in a DNS SAN. The leaf has `cA` false, and a
  critical key usage with `digitalSignature` and without `keyCertSign` or
  `cRLSign`. Its extended key usage has both `serverAuth` and
  `clientAuth`, because an exporter both serves and dials. The identity CA
  is a SPIFFE signing certificate: `cA` true, `keyCertSign`, and if it
  carries a SPIFFE ID, one with no path. Chains may include
  intermediates, so the CA can later be signed by an upstream authority.
  No key type is required beyond what TLS 1.3 supports.
- **Validation.** A peer validates as the X.509-SVID specification
  requires: it rejects a certificate with more than one URI SAN, chooses
  the trust bundle by the trust domain of the presented ID, runs RFC 5280
  path validation against that bundle only, and checks the leaf
  constraints. It verifies the URI SAN, not a DNS name, so `PeerEndpoint`
  can be a Pod IP.
- **Bundles per trust domain.** Trust bundles are keyed by trust domain,
  encoded as the SPIFFE Workload API encodes them. This JEP uses one trust
  domain, but adding federated bundles later is another map entry, not a
  new field.
- **Issuer.** The controller signs with an identity CA that is separate from
  its serving certificates and from `ROUTER_KEY`. The operator mounts the
  CA, which it can generate or take from cert-manager, as it mounts serving
  certificates today. A trust bundle can list several CAs, so the CA can be
  rotated without a gap.
- **Replaceable source.** The direct path depends only on the exporter
  holding a valid SVID, its key, and the bundles, and the exporter reloads
  all three without restarting. Direct eligibility uses the expiry that
  the exporter reports, not a record of what the controller signed. A
  later JEP can therefore add other sources, such as the SPIFFE Workload
  API that SPIRE agents and Teleport's `tbot` serve, or files those agents
  write, without changing peers or the controller's role.
- **Lifetime and renewal.** Certificates are short-lived and renewed before
  they expire, in the same way the exporter renews its token today
  (`RotateToken`). The controller does not sign for a deleted exporter, and
  short lifetimes stand in for revocation lists.
- **Delivery.** In Kubernetes, the controller can also act as the signer for
  Pod certificates, so an exporter Pod mounts a certificate with the same
  identity and trust bundle without calling `SignExporterCertificate`.
- **One stream protocol.** The peer listener serves `RouterService.Stream`,
  authenticated by the certificate instead of a JWT. If the router later
  accepts the same certificates, both transports share one protocol and
  one authentication.
- **Scope.** This JEP uses the certificate only for the direct peer-to-peer
  connections between exporters, in Pods or on hosts, that carry the
  forwards a `LeaseTemplate` or an ad-hoc CLI lease declares. A peer
  listener accepts nothing else. Using it for exporter-to-controller and
  exporter-to-router connections, and adding client certificates, are left
  to a separate JEP (*Future Possibilities*), as are other certificate
  sources and federation with other trust domains.

### DD-26: Forwards carry TCP streams; datagram and segment ports are reported

**Alternatives considered:**

1. **TCP streams only, with each port reporting its transport.** Ports
   report `stream`, `datagram`, or `frame`, and forwards accept `stream`.
   Datagram and segment ports appear in reports and fail validation in a
   forward.
2. **UDP datagrams as frames on the existing peer stream.** A forward
   carries each datagram as one frame over the router or direct path.
3. **L2 segments in this JEP.** Join the `connects` ports of several
   members into one bridged segment.
4. **No transport in the report** until a later JEP needs it.

**Decision:** Option 1.

**Rationale:** Many automotive protocols use UDP, and some rely on multicast
on a shared segment. SOME/IP service discovery is the main case. ECUs
announce and find services by multicast, each Offer entry carries the IP
address and port where the service listens, and subscriptions and events
follow over unicast UDP or TCP. Two ECUs on different benches, or a physical
ECU and a virtual one in a Pod, can discover each other only if those
multicasts reach both of them and the addresses inside them are reachable
as written. DoIP vehicle announcement has the same shape, over UDP
broadcast.

Option 2 keeps datagram boundaries but not datagram behavior. Over TCP, one
lost packet delays every later one, which turns loss into latency and skews
service discovery timers. A forward also joins named ports, not members of a
multicast group, and it would still have to rewrite or proxy the addresses
that discovery messages embed. Option 3 is the right model for multicast,
but it needs a datagram transport, interfaces and privileges in each
exporter's network namespace, an address plan the members share (DD-9 notes
that Cuttlefish guests use identical ones), and isolation between leases.
That is a design of its own.

Option 4 would make a later datagram or segment feature change what existing
reports mean: every port reported before it would have to be read as a TCP
stream, whatever it carries. With option 1, a driver can describe a SOME/IP
service discovery endpoint today, `jmp get exporters --with ports` shows it,
and a later JEP enables datagram forwards or segments by relaxing
validation, without a schema change. The pieces already fit. `connects`
ports describe segments (DD-6), the `endpoints` list takes more than two
members (DD-13), and DD-25 certificates work for a datagram transport such
as QUIC or DTLS as well as for TLS over TCP.

SOME/IP methods and events over TCP can be forwarded today, when their
endpoints are configured rather than discovered.

### DD-27: Two undirected ports form one path

**Alternatives considered:**

1. **A path between two `connects` ports.** Exactly two `connects` stream
   ports form one bidirectional path. The controller chooses which end opens
   it, and each end keeps its local mode (dialing a service or listening for
   a client) to itself.
2. **Allow `provides`–`provides` and `requires`–`requires` forwards.** Any
   two ports of the same direction could be joined.
3. **Keep `connects` reported only**, with every undirected link left to the
   segment work in *Future Possibilities*.
4. **Undirected links with any number of members** in this JEP, so that
   traffic from each end reaches every other end.

**Decision:** Option 1.

**Rationale:** Some bench connections have no client and no server. A
serial cross-over between a DUT console on one bench and a companion on
another is a common case. Each bench usually exposes its UART through a
serial-over-TCP bridge that listens for a client. Neither end provides a
service to the other, so declaring one `provides` and the other `requires`
describes something that is not true. It also forces a lab to put a dialing
client on one bench to satisfy the model.

Option 2 would give up the check that DD-6 relies on. Two HCI controllers
both report `provides`, and joining them is a mistake the controller should
reject. `connects` keeps that check: a port pairs with another of the same
kind only if its driver declares that it can. Option 3 leaves the serial
case without a correct description. Option 4 needs the forward to copy
traffic from each end to every other, which DD-23 keeps out of forwards. It
is the segment model that DD-26 leaves to later work.

A path works like the other forwards. The only difference is who starts a
connection:

- **Initiator.** The controller makes the endpoint whose member name sorts
  first the initiator, and says so in `ForwardSetup`. Only the initiator
  calls `DialPeer`, and the other end receives `ForwardConnect`, as a
  providing exporter does.
- **Local mode.** A `connects` port is configured with a local service to
  dial, or with a `listen` address where a local client connects, and the
  exporter does not report which. An initiator in dial mode opens the path
  once it reaches its local service. In listen mode, it opens the path when
  its client connects. The other end, on `ForwardConnect`, dials its service
  or uses its local client. If no client is connected yet, it waits for one
  for a bounded time.
- **One connection at a time.** A path carries one connection. A
  listen-mode end accepts one local client at a time. When the connection
  closes, both ends close their local side, and the initiator opens the path
  again when its local side is ready: on the next client connection, or by
  dialing its service again with backoff while the lease lasts.
- **Same checks.** Both ports must be `socket` with transport `stream`, and
  any declared protocols must agree. A `connects` port can appear in only
  one forward, and a forward that mixes `connects` with directed ports, or
  names more than two `connects` ports, fails validation. The direct path,
  certificates, and router are used as for any other connection.

### DD-28: Exclusive claims enforced by the API server

**Alternatives considered:**

1. **A claim object per exporter and per exclusion group.** Its name is
   derived from what it claims, so the API server allows only one. A lease
   creates its claims in sorted order before writing its status, and deletes
   the ones it created if any create fails.
2. **Remember the controller's own claims** in memory until its cache shows
   them, as the Kubernetes ReplicaSet controller does with its
   "expectations".
3. **Read active leases from the API server**, bypassing the cache, before
   each bind.
4. **Lock through the `Exporter` object**, writing `status.leaseRef` with
   optimistic concurrency.
5. **Detect conflicts and recover**, by releasing the later lease or
   replacing its conflicting member.

**Decision:** Option 1, for scalar and member-form leases alike.

**Rationale:** Two leases must never control one DUT, not even briefly. This
matters most in a small, busy pool, where every exporter is contested.
Today, two leases can claim the same exporter (DD-1). A guarantee cannot
rest on the cache, on the reconciler running one worker, or on leader
election handing over cleanly. It has to come from the API server: object
names are unique within a namespace, and creating an object whose name
exists fails atomically.

Options 2 and 3 close the race only while exactly one controller process
writes claims. Leader election aims for that but does not guarantee it: an
old leader that stalls past its renew deadline can still write after a new
leader has started. Option 3 also adds an uncached list to every bind.
Option 4 turns a status field into a lock, but exporter status is also
patched by the exporter's heartbeat and by the exporter controller. Claims
would fail for unrelated writes, and merge patches skip the version check
anyway. Option 5 lets the scenario happen and repairs it afterwards: two
clients could power-cycle and flash one DUT before the repair, and recovery
must choose between swapping hardware under a running test and failing it.

How option 1 works:

- **Keys.** A lease claims every exporter it binds and every exclusion group
  those exporters belong to. Claiming the exporter as well as its group keeps
  the exporter exclusive even if an administrator moves it to another group
  mid-lease. A claim is named `exporter-` or `group-` followed by the first 32
  hex digits of the SHA-256 of the name, which fits within the 253-character
  limit for any exporter or group name. The claimed name itself is in the
  spec. A change to an exporter's group applies to new leases only. An
  administrator who adds a leased exporter to a group that another lease
  holds makes the two leases share that DUT until one ends, so groups
  should be changed only on exporters that are not leased.
- **Acquire, then write.** After assignment (DD-18), the controller creates
  the lease's claims in sorted name order. When all of them exist, it writes
  `status.members`, or `status.exporterRef` for a scalar lease, in one
  `Status().Update` as before. A lease's status is written only after it
  holds every claim, so no reader of lease status can see two leases on one
  exporter.
- **Roll back on conflict.** If a create fails because another lease holds
  the claim, the controller deletes the claims this lease created in that
  reconcile, sets `Pending` with reason `NotAvailable` naming the held
  exporter, and requeues. The next assignment sees the claim and picks around
  it. A lease never holds part of a set beyond one reconcile.
- **No livelock.** Every lease creates claims in the same global order, so
  two leases that need overlapping exporters both try the lowest shared claim
  first. One gets it and goes on. The other fails before it holds anything
  the first one needs, so neither can block the other in a cycle.
- **No rebinding.** A bound lease keeps its claims until it ends. Nothing
  releases or re-selects the members of a bound lease, so conflicts cannot
  set off a chain of rebinds. A member whose exporter goes away becomes
  `Lost` and keeps its claim (DD-3).
- **Release.** The reconcile that ends a lease deletes its claims after
  writing `status.ended`. Claims also carry a controller owner reference to
  their lease, so deleting the lease deletes them.
- **Stale claims.** A claim can outlive its lease, for example if the
  controller stops between ending the lease and deleting the claim. The next
  lease that needs the claim reads its owner from the API server, bypassing
  the cache. If that lease has ended or no longer exists, the claim is
  deleted with a UID precondition, so a claim another lease has created
  since is never removed, and the create is retried. `status.ended` never
  reverts, so an ended lease cannot come back and use the claim.
- **Restart between claims and status.** A lease that finds claims it
  already owns, left by an attempt that stopped before writing status,
  treats them as acquired.

`Exporter.status.leaseRef` follows the claim rather than lease status, so
an exporter points at its one holder (*Readers of `status.exporterRef`*).

Claims guarantee exclusivity, not fairness. In a busy pool, a lease that
needs four exporters at once can keep losing to leases that need one. Each
attempt it loses releases what it took, so it holds nothing while it waits,
but it may wait a long time. Ordering waiting leases, or reserving exporters
for one as they free up, is a scheduling feature (*Future Possibilities*).

## Design Details

### Deployment assumptions

Each exporter must own its network namespace and run one process per
exporter identity.

A virtual exporter can run in its own Pod, as proposed in the Cuttlefish
orchestration draft. A
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

### Template expansion

`ClientService.CreateLease` expands a request that names a template before
it writes the lease, using the caller's namespace:

1. Read the `LeaseTemplate`. A missing template returns `NOT_FOUND`.
2. Check the request: every excluded role and every `member_selectors` key
   names a template role, excluded roles are `optional`, no role is both
   excluded and narrowed, and no scalar selector, `members`, `constraints`,
   or `forwards` is set. A violation returns `INVALID_ARGUMENT` naming the
   role or field.
3. Copy `members`, set `excluded: true` on
   each excluded role, and AND each `member_selectors` entry into that
   role's selector. A narrowing selector applied to an `exporterRef`
   member is rejected.
4. Copy `forwards` and `constraints` unchanged.
5. Take `duration`, begin and end time, tags, and context from the request,
   falling back to `defaults`.
6. Set `spec.templateRef` to the template name and its
   `metadata.generation`, then create the lease through the existing path,
   so CEL validation and access policy apply unchanged.

Expansion reads one object and makes no scheduling decisions, so a request
for a template behaves like the same lease written out by hand. Because
templates share the lease types and CEL rules, a valid template expands into
a valid lease unless the request's own fields are invalid.

### Binding: one pass, one write

The existing `reconcileStatusExporterRef` generalizes to
`reconcileStatusMembers`. Each member's candidates come from the existing
pipeline, run once per member with its own selector or `exporterRef`. The
controller then assigns all members distinct exporters together (DD-18),
creates their claims (DD-28), and commits the result in one write:

```{mermaid}
flowchart TD
    resolve["Per member: resolve exporterRef or selector,<br/>drop disabled and policy-denied exporters"]
    precheck{"Every required member<br/>has a candidate?"}
    reject["Set that member's existing reason<br/>(NoAccess, ExporterNotFound, ...)<br/>Write no claims"]
    filter["Per member: drop offline, claimed, group-held,<br/>cleaning-up, and spot-held exporters<br/>Rank with orderApprovedExporters"]
    assign["Per constraint-value combination (DD-22):<br/>assign required members, then optional members<br/>Shortest augmenting path per member"]
    complete{"Some combination assigns<br/>every required member?"}
    classify["Report the first filter stage at which the<br/>required members cannot all be assigned<br/>Name the competing roles; write no claims"]
    requeue["Requeue"]
    claim{"Create claims in sorted order (DD-28)<br/>All created?"}
    rollback["Delete the claims just created<br/>Pending: NotAvailable"]
    bind["Set status.members and member states<br/>Set priority to the minimum member priority"]
    commit["Commit one atomic Status().Update()"]

    resolve --> precheck
    precheck -->|No| reject
    precheck -->|Yes| filter --> assign --> complete
    complete -->|No| classify --> requeue
    complete -->|Yes| claim
    claim -->|No| rollback --> requeue
    claim -->|Yes| bind --> commit
```

**Per-member checks.** The checks that look at one member alone run
unchanged and report their existing reasons, naming the role:
`ExporterNotFound`, `SelectorMismatch`, `ExporterDisabled`, `AllDisabled`,
`NoAccess`, and the spot-access `Unsatisfiable`. An optional member that
fails one is recorded as `Omitted` with that reason instead of failing the
lease. An `excluded` member skips these checks and is recorded as `Omitted`
with reason `Excluded`. A required member that fails one keeps member state
`Pending` with
the reason, and the lease becomes `Unsatisfiable` and ends, as a scalar
lease does for these reasons today.

**Assignment.** Members are processed in spec order, required members first.
Each member takes its best-ranked free candidate, which is the scalar path's
choice today. Only when none is free does the controller search
breadth-first for the shortest chain of already-assigned members that can
each move to another of their own candidates and free one up (an augmenting
path). Assigned members can be moved but are never unassigned, so an
optional member never displaces a required one. The result:

- binds whenever any assignment of the required members exists;
- binds as many optional members as any assignment allows, with spec order
  deciding which;
- is identical to taking each member's best free candidate in order whenever
  that binds every member, and moves the fewest members otherwise;
- is deterministic, because candidate order is total (name breaks ties).

The scalar path is the one-member case, so its choice is unchanged.

**Failure classification.** Each stage of the filter pipeline keeps a subset
of the previous stage's candidates. If the required members cannot all be
assigned, the controller reruns the assignment against each stage in turn
and reports the first stage that fails:

| Stage: candidates that are also... | Condition | Reason |
| --- | --- | --- |
| Matching, enabled, policy-approved | `Unsatisfiable` | `InsufficientExporters` |
| Registered and online | `Pending` | `Offline` |
| Not claimed by another lease or its exclusion group | `Pending` | `NotAvailable` |
| `Available`, not cleaning up | `Pending` | `NotReady` |
| Not held under spot access | `Pending` | `NotAvailable` |

With one member this reduces to "the first stage with no candidates", which
is today's behavior. `InsufficientExporters` only arises with several
members, for example two roles whose `exporterRef` names the same exporter,
and no requeue can resolve it. The message comes from the failed search: the
roles it reached qualify, between them, only for the exporters it visited,
one fewer than the roles. For example: *roles `phone`, `phone-2` need 2
distinct exporters; only 1 is available: `px-01`.* This names the actual
competitors, not whichever role happened to be processed last.

Each stage runs the full constraint search, so a stage fails only if no
combination of values assigns the required members. At the first stage, a
lease that could be assigned without its constraints but not with them
reports `Unsatisfiable` with reason `ConstraintUnsatisfiable`, naming the
constraint and the values tried. For example: *no value of `bus` is carried
by candidates of both `headunit` and `ecu`*. At later stages the reasons
are unchanged: two benches that each have one busy member report
`NotAvailable`, and a requeue can resolve it.

Exclusion groups extend the claim check (DD-16). A candidate is excluded if
another active lease holds its group, either by binding an exporter in it or
through the groups recorded in that lease's `status.exclusionGroups`. Several
members of one lease may bind exporters in the same group. The binding write
records every group touched by the lease's bound exporters, so later
membership edits cannot silently release a DUT mid-lease. Spot-access
eviction is not implemented on `main`. When it is, taking over a lease's
exporters means deleting that lease's claims and creating new ones, for the
whole group (DD-28).

The scalar path uses the same selection code with a synthetic member and
writes the result to `status.exporterRef` (DD-2).

Assignment is per lease and reads claims from the cache, so it can pick an
exporter that another lease has just claimed. Creating the claim catches
that: the create fails, the lease deletes the claims it took in that
reconcile and requeues, and the next assignment picks around the claim. No
lease is bound until it holds every claim (DD-28).

### Lease state

```{mermaid}
flowchart TD
    pending["Pending<br/>No exporters held"]
    assignable{"Required members assignable?"}
    unsatisfiable["Unsatisfiable<br/>No exporters held"]
    bound["All claims held, then members<br/>written in one status update"]
    forwards["Forwards validated and requested"]
    ready["Ready"]
    degraded["Degraded<br/>Members stay held"]
    ended["Ended"]

    pending --> assignable
    assignable -->|"Not now: requeue"| pending
    assignable -->|"Never"| unsatisfiable
    unsatisfiable --> ended
    assignable -->|Yes| bound
    bound --> forwards
    forwards -->|"All forwards listening"| ready
    forwards -->|"Forward invalid or failed"| degraded
    ready -->|"Member lost or forward failure"| degraded
    ready -->|"Release or expiry"| ended
    degraded -->|"Release or expiry"| ended
```

Conditions reuse the existing `LeaseConditionType` values — `Pending`,
`Ready`, `Unsatisfiable`, `Invalid` — with `ForwardsReady` and `Degraded`
added. `Unsatisfiable` and `Invalid` stay terminal: `reconcileStatusEnded`
ends a lease that carries either, which releases its exporters. `Invalid` is
therefore used only for a spec that can be rejected without binding. A fault
found after binding, such as a forward that fails validation, sets
`ForwardsReady=False` and `Degraded` instead, so the members stay held
(DD-12).

`Ready` for a member-form lease requires all required members bound and every
non-disabled forward set up: `Listening`, or `Connected` while a connection
is open. A forward whose optional endpoint was omitted
has explicit `Disabled` status and does not gate readiness. Expiry,
`status.ended`, the `jumpstarter.dev/lease-ended` label, and `spec.release`
retain their existing behavior.

### Readers of `status.exporterRef`

A member-form lease leaves `status.exporterRef` nil (DD-2). These controller
paths read it today. None changes for a scalar lease:

| Path | Today | For a member-form lease |
| --- | --- | --- |
| `ControllerService.Listen` | Admits only the exporter in `status.exporterRef`. Keeps one listener queue per lease name, and a new `Listen` replaces the previous one | Admits the exporter of any bound member. The queue is keyed by lease and exporter, so members do not displace each other |
| `ControllerService.Dial` | Dials the exporter in `status.exporterRef`, or fails with "lease not active" | Dials the exporter bound to `member_name` (DD-2) |
| `ExporterReconciler.reconcileStatusLeaseRef` | Sets `Exporter.status.leaseRef` from `status.exporterRef`, following whichever lease is listed last | Sets it from the exporter's claim, which names exactly one lease (DD-28) |
| `assignedLease`, used by `Status` and `UpdateLeaseHooks` | Accepts the exporter's lease only if `status.exporterRef` names the exporter | Accepts a lease that binds the exporter as a member. Each member keeps its own lease-hook record |
| Exporter-initiated release (`ReportStatus`, `ReleaseLease`) | The exporter in `status.exporterRef` may set `spec.release` | See *Unresolved Questions* |
| `reconcileSharedWithPolicies` | Filters `spec.sharedWith` by policy on `status.exporterRef`, and grants the full set while it is nil | Filters by policy on every bound member (*Security*) |
| Owner reference | The lease gets a controller owner reference to its exporter. An object can have only one | One non-controller owner reference per bound member. The lease is garbage-collected only when every member's exporter is deleted; one deleted member is `Lost` (DD-3) |
| `reconcileStatusBeginEndTimes` | Sets `status.beginTime` and `Ready` when `status.exporterRef` is set | Sets `status.beginTime` when the members bind. `Ready` follows *Lease state* |
| Lease-acquisition metric | Counts success when `status.exporterRef` becomes set | Counts success when the members bind |
| `kubectl get leases` | The `Exporter` column prints `status.exporterRef.name` | Empty. `jmp get leases` prints the exporter of each role |

### Forward validation and establishment

Validation happens after binding, because the report that proves a port
exists belongs to a bound exporter, not to a selector (DD-12). For each
`spec.forwards[]` entry the controller checks, against
`ExporterStatus.Devices[].Ports`:

1. Every endpoint names a declared member, on distinct members — enforced
   by CEL at admission, before this point. Endpoints on omitted members are
   set aside, and the remaining checks apply to the bound ones.
2. Every named port exists on its bound exporter, and each port
   resolves to one driver UUID because report registration enforces
   exporter-wide unique names.
3. **Direction resolves.** The bound endpoints either include at most one
   `PROVIDES` and otherwise only `REQUIRES` (DD-13), or report only
   `CONNECTS` in a forward that lists exactly two endpoints, an undirected
   path (DD-27). Mixing `CONNECTS` with other directions, or listing more
   than two endpoints for a path, fails. A path with one end set aside
   passes this step and is decided by the next one.
4. **Enough endpoints are bound.** One providing endpoint and at least one
   requiring endpoint are bound, or both ends of a path. If not, and an endpoint was set aside in
   step 1, the forward is `Disabled` naming the omitted role; this is how a
   forward whose provider was omitted is recognized. If no endpoint was set
   aside, the forward fails.
5. Every port has attachment `socket` and transport `stream`. `wired` and
   `wireless` ports, and `datagram` and `frame` ports, cannot be forwarded
   in this JEP (DD-14, DD-26).
6. Every requiring port that declares `protocol` agrees with the providing
   port, if that declares one too. The two ends of a path agree in the same
   way (DD-8).
7. No `requires` or `connects` port appears in more than one forward
   (DD-23, DD-27).

A failure marks the forward `Failed` with the reason, sets
`ForwardsReady=False` and `Degraded` on the lease, and leaves members bound
for inspection. It does not set `Invalid`, which would end the lease and
release them (*Lease state*). A disabled optional-member forward is not a
validation failure and receives no setup instruction or token.

Setup and connections follow *How a forward comes up*. Each connection's
router token is issued as `Dial` issues one: a random stream ID as `sub`,
`iss: https://jumpstarter.dev/stream`, `aud: https://jumpstarter.dev/router`,
and a short expiry that bounds only the time to open the stream.
`RouterService.Stream` validates and pairs it as it does today (DD-5).

The `requires` side makes the direct attempt first, with a short, bounded
timeout, and opens its router stream only if that attempt fails. The
`provides` side opens its router stream when the connect instruction
arrives and also accepts a direct arrival. It splices whichever completes
and closes the other. Direct connections use mTLS with exporter identity
certificates (DD-25): each side checks that the other's certificate chains
to the trust bundle and carries the identity the controller named. The
requiring side sends the connection ID only after the handshake, and the
`provides` side accepts each one once. A plaintext or
server-authentication-only connection is rejected. `Direct`
fails without fallback; `Router` skips the direct attempt. Status records
the transport of the most recent connection and the reason for any
fallback.

**Direct eligibility.** Both members must report the same non-empty
`NetworkZone`, both must hold an unexpired identity certificate
(`IdentityExpiresAt`), and the `provides` side must report a
`PeerEndpoint`. The zone
is an opaque value supplied by deployment configuration, such as one value
per cluster network. The controller does not infer reachability from IP
addresses. An unreachable peer falls back to the router in `Auto` mode.

**Interruption.** One accepted local TCP connection and one peer stream form
a single splice. If the stream closes, after a network interruption, router
restart, or ingress reload, the exporter closes that local connection and
never attaches a replacement stream to it. On the router path, a stream ends
for both peers when either one goes away
([#1328](https://github.com/jumpstarter-dev/jumpstarter/pull/1328)). The `requires` endpoint keeps its
listener open, so a driver that reconnects gets a new stream through
`DialPeer`. There is no standing peer path to re-establish. A cut stream
invokes an explicit endpoint-driver recovery hook for protocols that dial
only once or must restore state, such as restarting a projection server.
Forward state comes from the listener and its streams, without probing the
service.

**Failure modes and handling:**

| Failure | Behavior |
| --- | --- |
| Named port absent on a bound exporter | Forward `Failed`, lease `Degraded`; members stay bound for inspection |
| Two endpoints `provides`, or every bound endpoint `requires` with none omitted | Forward `Failed`, lease `Degraded`; direction cannot resolve (DD-6, DD-13) |
| Declared protocols disagree | Forward `Failed`, lease `Degraded` (DD-8) |
| A port's attachment is `wired` or `wireless` | Forward `Failed`, lease `Degraded` naming the port and its attachment (DD-14) |
| A port's transport is `datagram` or `frame` | Forward `Failed`, lease `Degraded` naming the port and its transport (DD-26) |
| A forward mixes `connects` with directed ports, or names more than two `connects` ports | Forward `Failed`, lease `Degraded` (DD-27) |
| One end of an undirected path is an omitted or excluded member | Forward `Disabled` naming the role (DD-27) |
| A `requires` port appears in two forwards | Forward `Failed`, lease `Degraded` (DD-23) |
| Protocols differ and at least one tag is absent | Validation passes; protocol errors may occur when data is exchanged (DD-8) |
| Forward references an undeclared member | Rejected by CEL at admission; lease never created |
| An omitted or excluded member leaves a forward without its provider or without any requiring port | Forward `Disabled` naming the role; no setup or token; lease may become `Ready` |
| An omitted or excluded member is one of several requiring endpoints | That endpoint is skipped; the forward serves the others (DD-23) |
| Duplicate port names in one exporter's reports | Exporter registration rejected with `INVALID_ARGUMENT` |
| `listen` address already bound on the exporter | Forward `Failed`, lease `Degraded` naming the port and address |
| Router stream drops mid-lease | That local connection closes and the forward returns to `Listening`; the driver's next connection gets a new stream |
| Ingress/proxy reload cuts the peer stream | As above, and endpoint drivers are notified through the recovery hook |
| `DialPeer` cannot reach the providing exporter's `Listen` stream | `UNAVAILABLE`; the `requires` side closes the local connection |
| Direct dial fails or times out | In `Auto`, fall back to the router and record the reason; in `Direct`, fail |
| A member's exporter disappears | Peer's stream resets; lease `Degraded` naming the role (DD-3) |
| Client releases the lease | Forwards torn down first, then the lease ends normally |
| Another lease holds a claim this lease needs | Claims created in this reconcile are deleted; `Pending`, `NotAvailable`; requeue (DD-28) |
| A claim outlives its lease | Deleted by the next lease that needs it, after a live check of its owner, with a UID precondition (DD-28) |
| The controller stops between creating claims and writing status | The next reconcile treats the claims the lease owns as acquired (DD-28) |
| Requested template does not exist | `CreateLease` returns `NOT_FOUND`; no lease created |
| Request excludes a required role, or narrows an unknown one | `CreateLease` returns `INVALID_ARGUMENT` naming the role (DD-21) |
| Typed class disagrees with its template | Client raises `TypeError` before `CreateLease` (DD-17) |
| No label value satisfies a constraint | Lease `Unsatisfiable`, reason `ConstraintUnsatisfiable` (DD-22) |
| Constraint values give more than 1,024 combinations | Lease `Unsatisfiable`, reason `ConstraintLimit`; no search is run (DD-22) |
| Template edited or deleted mid-lease | No effect on existing leases (DD-19) |

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
- **Shared access:** lease sharing (`spec.sharedWith`) covers the whole
  lease. A shared client is granted access only if `ExporterAccessPolicy`
  allows it on every bound member; `status.sharedWith` records the result,
  and `Dial` checks it whichever `member_name` is requested. The current
  check reads only `status.exporterRef` and grants the requested set while
  that is nil, so it must change before a member-form lease can be shared.
- **Forward authorization:** `DialPeer` verifies that the caller is the
  exporter bound to the named member and that the member is a requiring
  endpoint of the forward, or the initiator of an undirected path. It
  issues one token per connection, to that exporter and the other end
  only, and the token is as short-lived as `Dial`'s.
  Access is limited to the explicitly named peer and port.
- **Port exposure:** only declared ports can participate in forwards. A
  `requires` listener uses an exporter-configured address and exists only
  while leased.
- **DUT exclusivity:** holding an exclusion group does not grant access to
  the group's unbound exporters. They cannot be dialed through the lease
  and do not receive `status.leaseRef`, so they cannot release it. Only
  administrators assign groups; an exporter cannot join a group through its
  own registration labels, so it cannot block unrelated exporters (DD-16).
- **Direct authentication and confidentiality:** the optional peer listener
  carries only the forwards of member-form leases, the inter-exporter bench
  networking a `LeaseTemplate` declares. It is disabled by default and
  requires mTLS with exporter identity
  certificates (DD-25). Each side verifies that the other's certificate
  chains to the trust bundle and names the exporter the controller
  announced, and the providing side admits only announced connection IDs,
  once each. No private key or bearer secret crosses the network. The
  listener is separate from device ports. Network policies
  should allow the authenticated peer port while blocking peer access to
  unauthenticated simulator ports. The Cuttlefish orchestration draft is expected to supply this policy
  with exporter Pods.
- **Identity CA:** the controller's identity CA can sign a certificate for
  any exporter in its trust domain, so its key needs the custody of the
  router signing key. It is kept separate from both that key and the
  serving certificates, and certificates are short-lived (DD-25).
- **Claims:** only the controller creates or deletes `ExporterClaim`
  objects. Clients and exporters have no access to them, so neither can
  take or release a claim, or block an exporter by creating one (DD-28).
- **Membership:** `members`, `constraints`, `forwards`, and `templateRef`
  are immutable after creation.
- **Templates:** only administrators create or change `LeaseTemplate`
  resources; `ClientService` exposes them read-only, within the caller's
  namespace. A template grants no access: expansion produces an ordinary
  request that per-member policy evaluates (DD-12). `docs` and `image` are
  links that the controller never fetches; clients display them as links,
  and display `instructions` as plain text, never as commands.
- **Ad-hoc requests:** the CLI-only rule for ad-hoc members is a client API
  boundary, not an authorization control. A client that builds
  `CreateLease` by hand can still send `members`, and access policy is what
  limits it (DD-20).
- **Physical RF:** devices in a shared lab are audible to others in range;
  lease authorization does not isolate radio traffic.
- **Simulator control:** standalone rootcanal listens on `0.0.0.0` without
  client authentication. Keep its test channel private because it can
  re-address devices, join controllers, and trigger the crash described in
  DD-9. Drivers may expose HCI and link ports through authorized forwards;
  network policy must block direct access to the underlying ports.

### Observability

JEP-0013's correlation fields gain `lease_member` and, for leases expanded
from a template, `lease_template`, beside the existing `lease_id`, `client`,
and `exporter`. Like `lease_id`, they stay in log bodies and exemplars and
are not metric labels. The lease-acquisition metric records the member
count and counts a member-form lease as acquired when its members bind;
today it keys on `status.exporterRef`.
Per-forward telemetry records bytes in each direction, connection and
cut-stream counts,
selected transport, and direct-dial fallback rate. Same-zone fallback can
indicate an unavailable peer listener, incorrect zone configuration, or a
blocking `NetworkPolicy`.

Cut streams also emit events for drivers that must restore protocol state,
such as the phone's projection server. Forward status exposes live
connections and degradation.

Where netsim supplies the simulated medium, `jumpstarter-driver-netsim` can
start, stop, and download pcap captures through its REST control API. These
captures can be attached to test results alongside forward metrics.

## Test Plan

### Unit Tests

- **CEL validation** on `Lease` and `LeaseTemplate`: the extended top-level
  one-of rule, mutual exclusion of the scalar and member forms, exactly one
  selection source per member, DNS-label role names, unique
  member and forward names, forwards and constraints naming declared
  members, two to eight forward endpoints on distinct members, `excluded`
  only on optional members, and immutability of `members`,
  `constraints`, `forwards`, and `templateRef`. The rules accept members
  that omit `optional`, `excluded`, or `selector`, and the generated CRDs
  install on the oldest supported Kubernetes version without exceeding
  the CEL cost budget.
- **Binding:** a reconcile that cannot assign every required member writes
  no claims. Two members with the same selector bind distinct exporters. The
  DD-18 case (A qualifies for `x` and `y`, B only for `x`) binds. An optional
  member never displaces a required one. Uncontended picks match first fit.
  Each failure-classification stage reports its reason and names the
  competing roles.
- **Constraints:** a shared value is chosen at bind time, an exporter
  without the label never satisfies a constraint, an omitted optional member
  is ignored, and `ConstraintUnsatisfiable` and `ConstraintLimit` are
  reported (DD-22).
- **Claims:** two leases reconciled back to back against a stale cache
  never both hold one exporter or group. A failed create deletes the
  claims taken in that reconcile and writes no status. Two leases that
  need the same exporters in opposite member order both finish without
  livelock. A controller stopped between claims and status resumes with
  the claims it owns. A stale claim is removed only after its owner is
  confirmed ended or gone, and never when a newer claim has replaced it.
  Moving an exporter to another group mid-lease does not release it
  (DD-28).
- **Exclusion groups:** a candidate whose group another active lease holds
  is skipped, one lease may bind several exporters of a group, and
  `status.exclusionGroups` survives a later edit of `spec.exclusionGroup`
  (DD-16).
- **Scalar regression:** existing lease controller tests pass unmodified and
  keep `status.exporterRef`. A one-member member-form lease fills
  `status.members` and leaves `status.exporterRef` nil.
- **Aggregation:** priority is the minimum member priority, and duration is
  bounded by the minimum member `maximumDuration`.
- **Request forms:** `CreateLease` rejects each mixed form with
  `INVALID_ARGUMENT` and creates no lease. The mixed forms are a scalar
  field with `members` or `template`, `template` with `members`,
  `constraints`, or `forwards`, and `constraints` or `forwards` without
  `members`.
- **Template expansion:** every step of *Template expansion*, including
  `NOT_FOUND`, each `INVALID_ARGUMENT` case, narrowing, exclusion, defaults,
  and `templateRef` provenance. Editing a template leaves existing leases
  unchanged.
- **Port reports:** declared ports, with direction, attachment, and
  transport, reach `ExporterStatus.Devices[].Ports`,
  an exporter reporting none registers unchanged, and duplicate names across
  driver reports reject registration.
- **Forward validation:** a missing port, two providing endpoints, no
  providing endpoint with none omitted, a protocol mismatch, and a `wired`,
  `wireless`, `datagram`, or `frame` port, a `connects` port mixed with
  directed ports, and three `connects` ports each mark the forward `Failed` and the lease `Degraded`
  without ending it. An omitted or excluded provider yields `Disabled` and does
  not gate `Ready`.
- **`Dial`:** omitting `member_name` on a member-form lease returns
  `INVALID_ARGUMENT` listing the roles, a valid role reaches its exporter,
  and a shared client is admitted only when policy allows it on every
  member.
- **`DialPeer`:** the caller must be the exporter bound to a requiring
  endpoint of the forward, or the initiator of an undirected path. Each call creates a new stream ID, returns its
  token, and delivers the same token to the providing exporter in
  `ForwardConnect`. Two concurrent connections on one forward pair
  separately. The call fails with `UNAVAILABLE` when the providing
  exporter is not listening. Existing
  router tests pass unmodified.
- **Identity certificates:** `SignExporterCertificate` sets the identity
  from the authenticated exporter whatever the CSR requests, and rejects a
  deleted exporter. Issued certificates and the identity CA pass X.509-SVID
  validation with an independent SPIFFE library. Renewal before expiry
  works without an exporter restart, and an expired reported
  `IdentityExpiresAt` makes the exporter ineligible for direct forwards
  (DD-25).
- **Undirected paths:** two `connects` ports join for each combination of
  local modes (dial–dial, dial–listen, listen–listen). Only the initiator
  calls `DialPeer`, a path carries one connection at a time, and it
  reopens after a cut (DD-27).
- **Peer listener:** rejects a dialer without a certificate, with one from
  another trust bundle, with more than one URI SAN, with a different
  exporter's identity, with an unannounced connection ID, or with a
  connection ID already used. A certificate whose SPIFFE ID uses a path
  other than the controller's default is accepted when the controller
  names that ID, which shows that peers treat IDs as opaque.
- **Fan-out:** a forward with three endpoints serves both required ports,
  an omitted requiring endpoint is skipped, and a `requires` port named by
  two forwards fails validation (DD-23).
- **`Listen`:** setup, teardown, and connect instructions decode beside the unchanged
  client-connection fields, each member of one lease keeps its own listener,
  and an exporter reporting no ports never receives a forward instruction.
- **Python client:** a selector yields a bare client and a `LeaseMembers`
  subclass yields that class. The pre-request check raises `TypeError` for
  each kind of disagreement between class and template, `connect()` raises
  for a missing driver (DD-17), and a call into a `Lost` role raises (DD-3).
  `LeaseMembers.metadata` and `env_members()` report each role's exporter,
  and `env()` fails in a member-form shell (DD-24).

### Integration Tests

Against a kind cluster with the controller and mock exporters (`e2e/`):

- Two mock exporters, one template with two members, and one forward from
  an `EchoNetwork` provided port to a required port, established through the
  real router, with byte fidelity and reset behavior checked. Several
  concurrent connections on that forward each echo their own data.
- Contention: a small pool and more concurrent scalar and member-form
  leases than it can serve, run for many rounds with the controller
  restarted and its leader changed mid-run. At every point, every lease is
  fully bound or holds nothing, and no exporter or group has two holders
  (DD-28).
- Expiry, release, and client disconnect leave no router streams, claimed
  exporters, or bound listeners.
- Transport: `Auto` goes direct for two same-zone exporters only after
  mutual TLS identity verification, falls back to the router when the peer
  dial is blocked, and records the transport used. `Direct` never falls
  back.
- CLI: `jmp get lease-templates`, `jmp describe lease-template`,
  `jmp shell -t`, ad-hoc `--member` and `--forward`, and
  `jmp get leases -o mobly` checked against Mobly's testbed schema.
- Compatibility: an N-1 client against an N controller for the scalar
  workflow, an N client requesting a template from an N-1 controller, and an
  N-1 exporter, which reports no ports, registering against an N controller.

### Hardware-in-the-Loop Tests

- **Virtual devices:** two Cuttlefish exporters in separate Pods pair over
  forwarded rootcanal ports. Phase 4 adds Wi-Fi association and netsim pcap
  capture where supported. Run on KVM-capable CI nodes.
- **Virtual device and peer:** connect a Cuttlefish exporter to `bt-peer`
  and assert pairing. With HFP Audio Gateway enabled, assert that an
  incoming call reaches the head unit's telephony stack. This test needs
  one guest and should run on each merge.
- **Virtual projection:** connect a phone CVD and `projection-rx` in
  separate Pods through `projection-wifi`. Assert that the receiver reaches
  the launcher and matches an expected screenshot.
- **Physical devices:** run a phone and head unit on exporters on different
  lab hosts, bound through an `rf-domain` constraint, on a labeled runner
  with the required hardware.
- **Latency:** publish HCI round-trip distributions for router, direct, and
  host-local paths, including cross-node and sustained A2DP runs.
- **Forward resilience:** cut a live stream through a router restart or
  ingress reload. Assert that the affected local TCP connection closes, a
  later local connection receives a new peer stream, events are emitted, and
  the endpoint-driver recovery hook restores protocols that do not reconnect
  themselves.

### Manual Verification

- `jmp shell -t` and `jmp shell --lease`: role-prefixed driver calls,
  `j-forward status`, and Ctrl+C teardown leaving no held leases.
- Port discovery: a user who has never seen an exporter's configuration can
  write a working forward from `jmp get exporters --with ports` alone.
- An existing Mobly multi-device suite run unmodified against an exported
  testbed.
- `jmp get leases` and `kubectl get leases` on a mix of scalar and
  member-form leases.

## Acceptance Criteria

**Lease plane**

- [ ] `spec.members[]`, `spec.constraints[]`, and `status.members[]` on
      `Lease`, with the extended CEL validation and immutability
- [ ] Binding is one `Status().Update`: a reconcile that cannot assign or
      claim every required member writes no status and deletes any claims
      it created
- [ ] No two active leases hold one exporter or exclusion group, under
      concurrent requests, a stale cache, a controller restart mid-bind,
      and a leader change (DD-28)
- [ ] Two members with identical selectors bind two distinct exporters, and
      a lease binds whenever any assignment exists (DD-18)
- [ ] A `sameLabel` constraint binds members that share a value chosen at
      bind time (DD-22)
- [ ] At most one lease holds an exclusion group (DD-16)
- [ ] `ExporterAccessPolicy` is evaluated per member, for the owner and for
      shared clients
- [ ] Lease priority is the minimum member priority; duration is bounded by
      the minimum member `maximumDuration`
- [ ] `status.exporterRef` is unchanged for scalar leases and nil for every
      member-form lease; existing scalar controller tests pass unmodified
- [ ] `Dial` without `member_name` on a member-form lease returns
      `INVALID_ARGUMENT` naming the roles
- [ ] Every path in *Readers of `status.exporterRef`* handles member-form
      leases

**Templates and clients**

- [ ] `LeaseTemplate` CRD, with `GetLeaseTemplate` and `ListLeaseTemplates`
      on `ClientService`
- [ ] `CreateLease` accepts exactly one request form (scalar, template, or
      members) and rejects mixed forms with `INVALID_ARGUMENT` before any
      exporter is selected
- [ ] `CreateLease` expands a template as in *Template expansion*; an edited
      or deleted template does not affect existing leases (DD-19)
- [ ] Requests can narrow selectors, exclude optional roles, and set
      duration, begin and end time, tags, and context, and nothing else
      (DD-21)
- [ ] `jmp get lease-templates`, `jmp describe lease-template`, `-t` on
      `jmp shell` and `jmp create lease`, and the CLI-only ad-hoc flags
      (DD-20)
- [ ] `config.lease(M)` yields a typed `LeaseMembers` instance, and the
      pre-request check rejects a class that disagrees with its template
      (DD-17)
- [ ] `JumpstarterTest` accepts a `members` class
- [ ] A member-form shell sets `JMP_LEASE_MEMBERS`, `j` exposes roles,
      `j-forward` reports forwards, and `LeaseMembers.metadata` names each
      role's exporter (DD-24)

**Ports and forwards**

- [ ] `PortReport` is optional on `DriverInstanceReport`; exporters
      reporting no ports register and operate unchanged
- [ ] Port names are unique across an exporter; duplicates reject
      registration, and setup names the resolved driver UUID
- [ ] Declared ports appear in `ExporterStatus.Devices[].Ports` and in
      `jmp get exporters --with ports`
- [ ] `listen` addresses never appear in a report, CR status, or lease spec
- [ ] Forward validation rejects missing ports, unresolvable directions,
      non-socket and non-stream ports, and declared-protocol mismatches,
      naming the
      forward, without ending the lease
- [ ] A forward left without its provider or any requiring port by an
      omitted or excluded member is `Disabled`, receives
      no credentials, and does not prevent `Ready`
- [ ] `ListenResponse` carries setup, teardown, and per-connection connect
      instructions
- [ ] Each forwarded connection gets its own stream ID from `DialPeer`, and
      `RouterService` is unchanged (DD-5)
- [ ] A provided port serves several required ports, listed in one forward
      or across several (DD-23)
- [ ] Two `connects` ports form one bidirectional path, whatever their
      local modes (DD-27)
- [ ] Exporters obtain identity certificates for their own keys through
      `SignExporterCertificate`, with the identity set by the controller;
      the certificates are valid X.509-SVIDs (DD-25)
- [ ] The direct path uses mTLS with exporter identity certificates, admits
      only announced connections from the announced exporter, falls back
      in `Auto` mode, and records the transport used
- [ ] `bt-peer` participates as a `requires` endpoint through exporter
      configuration alone
- [ ] After a peer-stream cut, the endpoint closes the affected local TCP
      connection; a later connection gets a new stream, and no replacement
      stream is attached to an existing local connection
- [ ] A second exporter process registering under a live identity is
      refused

**Topologies** (each a phase gate, in order)

- [ ] **Phase 1, virtual Bluetooth:** two CVDs in Pods on separate nodes
      complete BR/EDR discovery and pairing through federated rootcanals or
      a shared HCI instance, in CI
- [ ] **Phase 2, virtual projection:** a phone CVD and `projection-rx` in
      separate Pods reach the projection launcher over a forwarded port, in
      CI without lab hardware
- [ ] **Phase 3, physical devices across hosts:** a phone and head unit on
      exporters on different lab hosts complete a projection session
- [ ] **Phase 4, virtual Wi-Fi:** two CVDs in separate Pods associate
      through bridged `mac80211_hwsim`/`wmediumd` or a shared netsim 802.11
      chip and complete the Bluetooth-to-Wi-Fi projection handover
- [ ] Router-forward HCI latency measurements and supported workload limits
      are published, including cross-node and sustained A2DP runs

DD-9 and DD-10 record the manual prototype results. Automated CI and
multi-node verification remain outstanding.

## Backward Compatibility

The schema and protocol changes are additive. DD-2 defines compatibility
for `status.exporterRef`.

- **CRD**: `Lease` gains three optional spec lists (`members`,
  `constraints`, `forwards`), an optional `templateRef`, and three optional
  status lists (`members`, `forwards`, `exclusionGroups`). New
  `LeaseTemplate` and `ExporterClaim` CRDs are added. The controller's role
  gains read access to templates and create, read, and delete access to
  claims. `ExporterStatus.Devices[]`
  gains an optional `ports` list, and `ExporterStatus` gains optional
  `peerEndpoint`, `networkZone`, and `identityExpiresAt`. No existing field
  changes type, meaning,
  or default. `Exporter` gains one optional spec field, `exclusionGroup`;
  exporters without it behave as today. `ExporterAccessPolicy`, `ExporterSet`, and `VirtualTargetClass` are
  untouched. Every lease that exists today validates unchanged.
- **`status.exporterRef`**: unchanged for every lease that does not pass
  `members`. Every member-form lease, including an explicit one-member list,
  leaves it nil, which existing consumers already read as "not bound yet"
  (DD-2), so the Cuttlefish orchestration façade, `jmp get leases`, `Dial` and JEP-0013
  telemetry keep working; they change only to *support* member-form leases.
- **Driver report and drivers**: `ports` is a new optional repeated field, so
  an exporter built before this JEP reports none and is treated as
  unable to participate in forwards. An unset `attachment` means `socket`.
  Ports are declared in exporter configuration; `bt-peer` needs no Python
  changes to use a forwarded HCI endpoint.
- **Protocol**: new fields on existing messages, two `ClientService` RPCs,
  and two `ControllerService` RPCs. `RouterService` is untouched, and so
  is the existing token authentication: an exporter that never requests an
  identity certificate uses the router path only. Unknown fields are ignored by proto3, so
  an N-1 client talks to an N controller unchanged. An N client sending
  `members` or `template` to an N-1 controller sends no `selector` or
  `exporter_name`, which the existing `CreateLease` validation already
  rejects with `INVALID_ARGUMENT`. No one-device lease is created by
  mistake. The client still maps that error, or `UNIMPLEMENTED` from
  `ListLeaseTemplates`, to a message saying the controller predates
  member-form leases. The legacy `ControllerService` lease RPCs are
  unchanged.
- **Operator upgrade**: a CRD schema addition, a standard bundle bump with no
  conversion webhook. Member-form leases must be removed before rollback;
  scalar leases remain compatible. After an upgrade, the controller creates
  claims for the leases that are already bound, oldest first, before it
  binds any new lease. If the old race left two leases on one exporter, the
  newer lease marks that member `Lost` (DD-3); this is the only case in
  which a conflict is resolved after the fact.
- **Scalar exclusivity**: scalar leases take claims too, so they gain the
  same guarantee. On `main`, two scalar leases can briefly hold one exporter
  (DD-28).
- **Coexistence**: scalar- and member-form leases share one exporter pool,
  one scheduler, one selection implementation, and one set of claims.

## Consequences

### Positive

- One lease acquires all required members in one status write and gives them
  a shared lifetime.
- No two leases ever hold one exporter or exclusion group, whatever the
  cache or controller state, and a bound lease is never re-selected (DD-28).
- Wiring knowledge lives in one admin-maintained template per lab. Tests
  name a template and its roles, so a test runs unchanged in every lab that
  publishes that template, and a rewired bench needs one template edit.
- Constraints let a request use any free bench with the right wiring
  instead of naming one.
- Templates are discoverable with their documentation through
  `jmp get lease-templates` and `jmp describe`.
- Exporters can run on different hosts while retaining role-based access,
  lease policy, and telemetry.
- Forwards reuse existing stream primitives and an unchanged
  `RouterService`; named ports
  are discoverable through exporter reports.
- The port model supports Bluetooth, projection, CAN, and serial connections
  without adding protocol-specific logic to the controller.

### Negative

- Consumers must handle scalar and member-list lease forms.
- A test cannot define a new multi-exporter setup on its own; it needs an
  administrator to publish a template, or a lease created with the CLI
  (DD-20).
- A typed class and its template can drift apart. The pre-request check
  fails fast but only at run time. Generated classes, which need
  ExporterClass, would avoid the drift.
- Constraint search multiplies binding work by the number of value
  combinations, up to the combination limit (DD-22).
- Protocols that discover peers over UDP multicast, such as SOME/IP service
  discovery, cannot yet span exporters. Tests need configured endpoints,
  or devices that share a physical segment (DD-26).
- Port declarations add configuration, and fixed listeners depend on network
  namespace isolation.
- Bind-time port validation holds devices even when a forward is invalid.
  Omitted protocol tags allow compatibility errors to surface at runtime.
- Individual members cannot be released early.
- Binding writes one claim per bound exporter and group. A lost race costs
  those creates and their deletes (DD-28).
- Freeing an exporter for a later member can move an earlier member to a
  lower-ranked candidate, which can lower the lease's priority (the minimum
  member priority). Binding with lower priority is preferred to not binding
  (DD-18).
- Forwarding adds local listeners and, optionally, an authenticated peer
  listener to exporters.
- The controller becomes a certificate issuer, with a CA key to protect
  and rotate (DD-25).
- Every forwarded connection costs one controller round trip, and new
  connections need the controller to be reachable (DD-5).
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
  long-lived connections. The local connection closes and a recovery event
  is emitted; tests
  must cover deliberate stream loss. Direct connections avoid the ingress
  path where available.
- **Deployment isolation:** shared network namespaces can cause listener
  collisions and simulator resets across leases. Enforce the documented
  deployment assumptions and report bind failures clearly.
- **Binding contention:** in a small, busy pool, a lease with many members
  can wait behind leases that need fewer. Claims keep every lease exclusive
  and every waiting lease empty-handed, but do not order waiting leases
  (DD-28).
- **Consumer compatibility:** readers may assume every bound lease has
  `status.exporterRef`. *Readers of `status.exporterRef`* lists the
  controller paths found so far; other consumers must be checked before
  release.
- **Upstream interfaces:** netsim and vhost-user integration may change.
  Pin runtime images and track upstream compatibility.
- **Identity CA compromise:** a leaked identity CA key lets an attacker
  pose as any exporter on the direct path. Separate custody, short
  certificate lifetimes, and a multi-CA trust bundle for rotation limit
  the damage (DD-25).
- **Capacity:** per-member policy and the eight-member limit bound access;
  there is no quota across a lease's members.
- **Template sprawl:** labs may publish many near-identical templates for
  small variations. Request-time narrowing (DD-21) covers selector variants;
  the remaining cases may need parameterized templates later.

## Rejected Alternatives

DD-1 through DD-28 record the API, transport, binding, and template
alternatives.
Higher-level alternatives are:

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
- **Separate JEPs for leases and templates:** templates were first planned
  as a follow-up. Without them, the typed Python API and the CLI would each
  ship an ad-hoc way to declare members that DD-20 would later have to
  withdraw.

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
  claims live on separate objects. Lease claims are separate objects too,
  but one controller takes them in a fixed order and releases them on any
  conflict, so a lease never holds part of its set (DD-28).
- **Kubernetes controller expectations** let the ReplicaSet controller
  account for its own writes before its cache shows them. DD-28 considers
  this and chooses claims that the API server enforces instead.
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
- **Listener allocation:** keep fixed addresses with collision validation,
  or allocate ephemeral ports and pass the address to the driver.
- **Synchronization:** determine whether client-side barriers are sufficient
  or independently controlled roles need a controller-mediated barrier.
- **Mobly endpoints:** decide whether exported ADB endpoints depend on a live
  shell session or on longer-lived client-managed forwards.
- **Template access:** decide whether every client in a namespace may use
  every template, or whether templates need an allow list or a policy hook
  alongside `ExporterAccessPolicy`.
- **Ad-hoc gating:** decide whether ad-hoc member-form requests should be
  limited by policy, for example to administrators or interactive clients
  (DD-20).
- **Virtual members:** decide whether a template member can name an
  `ExporterSet` (JEP-0014) so that physical and virtual members are declared
  in one template.
- **Lease sharing:** sharing
  ([#942](https://github.com/jumpstarter-dev/jumpstarter/pull/942)) covers
  the whole lease (*Security*). Decide whether a template can set sharing
  defaults, and whether sharing should be limited to some members.
- **Exporter-initiated release and hooks:** an exporter can end its lease
  today, through `ReleaseLease` or a lifecycle hook whose failure action
  ends the lease. Decide whether one member doing so ends the whole lease
  or marks that member `Lost` (DD-3), and whether `Ready` waits for every
  member's `beforeLease` hook.
- **Template hooks:** decide whether `defaults` can name exporter-side
  lifecycle hooks, or whether hooks stay on exporters only.

To resolve during implementation:

- The direct-dial timeout before router fallback.
- Identity certificate lifetime and renewal point, and whether the
  operator generates the identity CA or takes it from cert-manager.
- How exporters report forward endpoint state to the controller: listener
  bound, bind failure, live connections, and transport in use.
- How deployments supply and validate `NetworkZone`; the proposed default
  is deployment configuration.
- Protocol-specific reconnect behavior, including projection-server restart
  and persistence of Bumble bond keys.
- How Phase 2 CI obtains vendor artifacts without redistributing them.
- Readable multi-member output in `jmp get leases`.
- How `AVAILABLE` in `jmp get lease-templates` is computed cheaply for
  large namespaces, and whether it should run the joint assignment.
- The constraint combination limit (proposed 1,024).

## Future Possibilities

The following work builds on this JEP:

- **Priority-aware assignment:** when several complete assignments exist,
  prefer the one with the highest minimum member priority (bottleneck
  matching) rather than the fewest moves (DD-18).
- **Shared infrastructure exporters:** exporters that serve several other
  exporters and leases, such as network switches, relay matrices, signal
  gateways, RF enclosures, and programmable power supplies. Such an exporter
  maps its channels to other exporters' ports, allocates them per lease, and
  isolates them at release. This work would allow forwards over `wired` and
  `wireless` ports, including buses with more than two endpoints and a
  fidelity level that separates electrical connections from frame-forwarding
  gateways. It would also add scoped access to shared channels, such as one
  power output (DD-14).
- **Richer constraints:** add rules beside `sameLabel` in the
  `LeaseConstraint` oneof, such as anti-affinity (`differentLabel`), label
  expressions, or a topology of buses and RF domains (DD-22).
- **Parameterized templates:** let a template declare parameters, such as
  an Android version, that a request fills in, beyond selector narrowing
  (DD-21).
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
- **Selection-time port validation:** expose reported ports through dynamic
  exporter labels so an unsatisfiable request holds no exporters (DD-12).
- **Typed members through ExporterClass:** once the ExporterClass proposal
  lands, each member can pin its exporter to a class with
  `exporterClassName` and `exactMatch`, the fields that proposal adds to
  `Lease.spec`. A projection template could then require a phone class on
  `phone` and a head unit class on `headunit`. As in that proposal, the
  class is AND-combined with the member's `selector` or `exporterRef`, or
  used alone. The per-member CEL rule therefore gains a third selection
  source, which relaxes validation and leaves stored leases valid.
  Binding considers only exporters that satisfy each member's class, so a
  member whose exporter lacks a driver holds nothing. The typed Python
  class can then be checked against the classes before the request, and
  `jmp describe lease-template` can show each member's interfaces. Under
  DD-21, a request could narrow a member to a subclass of its template's
  class.
- **Polyglot codegen from templates:** with members typed by
  `ExporterClass`, the codegen pipeline that produces per-interface clients
  from JEP-0011 protos can generate the typed lease from a template: the
  Python `Projection` class above, or a TypeScript, Java, Kotlin, or Rust
  type with one accessor per role and per forward. Names follow each language's convention, for example
  `bt_peer` becomes `btPeer`. `jmp describe lease-template NAME -o python`
  could emit the class directly. Generated clients request the lease with
  `CreateLease` and `template`, and dial each role with
  `DialRequest.member_name`.
- **Fair scheduling:** order waiting leases by priority and age, and
  reserve exporters for a waiting lease as they free up, so that leases
  with many members are not starved in busy pools (DD-28).
- **Network segments:** join the `connects` ports of more than two members
  into one L2 segment, for example VXLAN over UDP, so that multicast and
  broadcast protocols work across exporters with one address plan. The
  first targets are SOME/IP service discovery between physical or virtual
  ECUs and DoIP vehicle announcement. The same model covers Bumble relay
  rooms. It needs datagram transport, per-lease isolation, and network
  privileges in exporter namespaces (DD-6, DD-26).
- **Ephemeral listeners:** allocate addresses dynamically if shared network
  namespaces are supported later.
- **Multi-exporter policy and quota:** add limits across members and support individual
  member release if needed.
- **Pod certificate delivery:** Kubernetes 1.37 graduates Pod Certificates
  and ClusterTrustBundles to Stable. With the controller as the signer,
  exporter Pods can mount a kubelet-managed key, the DD-25 identity
  certificate, and the trust bundle, renewed automatically, without
  calling `SignExporterCertificate`. This needs live certificate reload in
  the exporter; exporters outside Kubernetes keep the CSR path.
- **mTLS for every connection (separate JEP):** authenticate
  exporter-to-controller and
  exporter-to-router connections with the DD-25 identity certificate
  instead of bearer tokens, and issue client identity certificates in the
  same form, so the router can verify both ends of a stream. Lease, forward,
  and access-policy authorization would still apply. The JEP-0013
  telemetry service, which already plans to verify identities through
  mTLS, could use the same certificates. The bearer credential would remain
  only to enroll a new exporter or client.
- **SPIFFE infrastructure:** accept identities from the SPIFFE Workload
  API (SPIRE, Teleport's `tbot`) as well as from the controller, map
  `Exporter` resources to IDs those systems issue, and federate trust
  bundles with other trust domains through SPIFFE bundle endpoints. DD-25
  keeps IDs opaque to peers and bundles keyed by trust domain, so this
  needs no change to the direct path.
- **Datagram forwards:** carry UDP between `datagram` ports, and frames
  between `frame` ports, over a datagram transport without TCP head-of-line
  blocking. One example is QUIC with its unreliable datagram extension
  (RFC 9221) on the direct path, authenticated with DD-25 certificates.
  The Phase 4 Wi-Fi frame bridge needs the same transport (DD-26).
- **Vehicle-bus simulation:** integrate a restbus simulator as a provided
  socket alongside existing CAN, DoIP, SOME/IP, UDS, XCP, and OBD drivers.
- **Test-framework integration:** supply a device or Mobly-controller shim
  backed by a lease.
- **On-demand members:** provision JEP-0014 pool instances to satisfy a lease.

## References

- [JEP-0014: Virtual Scalable Exporters](JEP-0014-virtual-scalable-exporters.md)
  — "Composite leases — multiple exporters linked into one logical lease"
  (Future Possibilities)
- Cuttlefish Kubernetes-Native Orchestration (draft, not yet submitted;
  unnumbered, because JEP-0016 is now used by
  [#1272](https://github.com/jumpstarter-dev/jumpstarter/pull/1272)) — its
  DD-8, whose option 1 this JEP implements
- Dynamic Exporter Labels (draft, not yet submitted; unnumbered, because
  JEP-0017 is now used by
  [#1253](https://github.com/jumpstarter-dev/jumpstarter/pull/1253)) — the
  mechanism selection-time port validation depends on (DD-12)
- [JEP-0013: Metrics, Tracing, and Log Observability](JEP-0013-observability-telemetry-logs.md)
- [JEP-0011: Protobuf Introspection and Interface Generation](JEP-0011-protobuf-introspection-interface-generation.md)
  — the introspection direction port reporting extends
- Native gRPC Services (draft, not yet submitted) — reserves
  `DriverInstanceReport` field 7 (`native_services`)
- Polyglot Typed Device Wrappers (draft, not yet submitted) — the per-exporter
  codegen pipeline a lease template extends to multi-exporter leases
- ExporterClass Mechanism (draft, not yet submitted; unnumbered) — the
  `DriverInterface` and `ExporterClass` resources that will type lease
  members; this JEP defines no driver typing of its own
- [jumpstarter-dev/jumpstarter#47](https://github.com/jumpstarter-dev/jumpstarter/issues/47)
  — `LeaseTemplate`: reusable multi-exporter lease definitions
- [jumpstarter-dev/jumpstarter#942](https://github.com/jumpstarter-dev/jumpstarter/pull/942)
  — lease sharing (merged)
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
- SPIFFE specifications: [SPIFFE ID](https://github.com/spiffe/spiffe/blob/main/standards/SPIFFE-ID.md),
  [X.509-SVID](https://github.com/spiffe/spiffe/blob/main/standards/X509-SVID.md),
  [Trust Domain and Bundle](https://github.com/spiffe/spiffe/blob/main/standards/SPIFFE_Trust_Domain_and_Bundle.md),
  [Workload Endpoint](https://github.com/spiffe/spiffe/blob/main/standards/SPIFFE_Workload_Endpoint.md),
  and the [Workload API protobuf](https://github.com/spiffe/go-spiffe/blob/main/proto/spiffe/workload/workload.proto)
  — the identity format, certificate profile, and bundle encoding DD-25
  follows
- [Teleport Workload Identity](https://goteleport.com/docs/machine-workload-identity/workload-identity/introduction/)
  — a SPIFFE-compatible issuer with operator-chosen IDs (DD-25)

---

*This JEP is licensed under the
[Apache License, Version 2.0](https://www.apache.org/licenses/LICENSE-2.0),
consistent with the Jumpstarter project.*
