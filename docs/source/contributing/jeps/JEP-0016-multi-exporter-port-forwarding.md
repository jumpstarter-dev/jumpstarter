# JEP-0016: Multi-Exporter Port Forwarding

| Field             | Value                                                    |
| ----------------- | -------------------------------------------------------- |
| **JEP**           | 0016                                                     |
| **Title**         | Multi-Exporter Port Forwarding                           |
| **Author(s)**     | @kirkbrauer (Kirk Brauer, kbrauer@hatci.com)             |
| **Status**        | Discussion                                               |
| **Type**          | Standards Track                                          |
| **Created**       | 2026-11-23                                               |
| **Updated**       | 2026-11-23                                               |
| **Discussion**    | [PR #1393](https://github.com/jumpstarter-dev/jumpstarter/pull/1393), [PR #1069](https://github.com/jumpstarter-dev/jumpstarter/pull/1069) (first version, as a part of JEP-0015) |
| **Requires**      | JEP-0015                                                 |
| **Supersedes**    |                                                          |
| **Superseded-By** |                                                          |

---

## Abstract

This JEP lets a lease connect its members to each other. A driver declares
named ports in the exporter configuration, and the local addresses stay
there. A forward in the lease connects a port on one member to a port on a
different member. A forward sends TCP byte streams through the current
router. The router does not change, and the client is not in the data path.
This JEP uses the member-form leases and the lease templates of
[JEP-0015](JEP-0015-multi-exporter-leases.md).

## Motivation

JEP-0015 lets one lease hold many exporters. But the devices of those
exporters cannot send data to each other through Jumpstarter. The current
streams connect a client to an exporter. No managed path connects two
exporters.

Some tests must have such a path:

- A virtual phone and a virtual head unit are in different Pods. Their
  simulated Bluetooth controllers must connect to each other.
- A projection receiver in one Pod must connect to a projection server on a
  phone in a different Pod.
- Two ECUs on different hosts must use one CAN segment through a
  CAN-over-TCP bridge.
- Two serial lines on different benches must connect as a cross-over.

Today, the test must make these connections itself. It must know the
addresses and the port numbers of each lab. The connections also have no
relation to the lease. Thus they can stay open after the lease ends.

### Virtual devices and physical devices

The medium between two devices is in a different location for each type of
device:

| | Virtual devices | Physical devices |
| --- | --- | --- |
| Example | A Cuttlefish phone and head unit in different Pods | A phone and a head unit on two lab hosts |
| Medium | Simulator sockets on each exporter | A radio link or a cable |
| What connects the devices | A forward | The air or the harness |
| What a forward sends | The simulated medium, for example Bluetooth HCI | Only data that a bridge puts on a TCP socket, for example CAN through `socketcand` |

Physical radio devices use the air. They use only the shared lease of
JEP-0015 and no forward.

### User Stories

- **As a** CI maintainer, **I want to** pair virtual devices that run in
  different Pods. Projection tests then run on each change without lab
  hardware.
- **As a** lab engineer, **I want to** connect two ECUs on different hosts
  through a CAN-over-TCP bridge. A test can then use devices on different
  machines.
- **As a** lab administrator, **I want to** put the connections in a lease
  template. Users then get the correct connections without our addresses
  and port numbers.

## Proposal

### Terms

| Term | Meaning |
| --- | --- |
| Port | A named connection point on a driver. It is not a TCP port number. |
| `provides` port | A port with a local service. For each forwarded connection, the exporter connects to that service. |
| `requires` port | A port for a local driver that uses a service on a different member. The exporter opens a local listener for that driver. |
| `connects` port | A port that connects to a port of its own type. No end is the service. Two such ports make one path. |
| Forward | An entry in `Lease.spec.forwards[]`. It connects ports on different members. |
| Endpoint | One member and one port of a forward. |
| Path | A forward between two `connects` ports. |

JEP-0015 defines the terms member, role, member-form lease, bind, and
lease template. It also defines the member states `Bound`, `Omitted`, and
`Lost`. Each forward is a two-way stream. The direction of a port shows the
end that has the service, not the direction of the data.

### Scope

This JEP adds two concepts:

| Concept | Location | Function |
| --- | --- | --- |
| Ports | Exporter configuration and `DriverInstanceReport` | A driver declares the points where it can connect to a different exporter. |
| Forwards | `Lease.spec.forwards[]` and `LeaseTemplate.spec.forwards[]` | A forward connects a `provides` port to one or more `requires` ports, or two `connects` ports. |

This JEP includes:

- Port declarations and port reports, with a direction and an optional
  protocol tag (DD-3 to DD-5).
- Forwards for TCP byte streams through the router (DD-1, DD-2).
- One `provides` port for many `requires` ports (DD-10).
- A path between two `connects` ports (DD-11).
- Forward validation after the controller binds the lease (DD-7).
- CLI support, the `j-forward` command, and the Python `Forward` type.
- Reference topologies for virtual Bluetooth and virtual projection.

This JEP does not include:

| Item | What this JEP does |
| --- | --- |
| Direct connections between exporters | All forwards use the router. A direct path must have an exporter identity that a later JEP defines (DD-1). |
| Physical links, such as Ethernet, CAN, and LIN through relays or switches | Exporters report `wired` and `wireless` ports. A forward that uses one fails validation (DD-8). |
| UDP, multicast, and L2 segments, for example SOME/IP service discovery | Exporters report `datagram` and `frame` ports. A forward that uses one fails validation (DD-9). |
| A forward that copies data between `requires` ports | Each `requires` port has its own connections to the `provides` port (DD-10). |
| A connection between a virtual device and a physical radio | Not supported (*Future Possibilities*). |
| Simulated Wi-Fi between virtual devices | *Future Possibilities*. |
| A selector that selects exporters by their ports | The controller validates ports after it binds the lease (DD-7). |

### Ports

A driver declares its ports in the exporter configuration. Each port has a
name and a direction.

| Direction | Meaning | Example |
| --- | --- | --- |
| `provides` | A local service listens. | `rootcanal` on a Cuttlefish exporter: HCI on `127.0.0.1:7300` |
| `requires` | The driver connects to a local address. | `controller` on a `bt-peer` exporter: the address of its HCI controller |
| `connects` | No side has the primary function. | Two serial-over-TCP bridges on different benches |

A `TcpNetwork` child can supply a `provides` port:

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

For a `requires` port, the exporter opens a local listener. The driver
connects to this listener:

```yaml
export:
  bt_peer:
    type: jumpstarter_driver_bt_peer.driver.BtPeer
    config:
      transport: "tcp-client:127.0.0.1:7300"   # no change
    ports:
      - name: controller
        direction: requires
        listen: 127.0.0.1:7300                 # local; the exporter does not report it
        protocol: hci-h4                       # optional
```

The `bt-peer` driver connects to `127.0.0.1:7300`, as it does today. The
exporter sends that connection to the remote `rootcanal` service. The
driver code does not change.

A `connects` port gives one of two local addresses:

```yaml
    ports:
      - name: uart
        direction: connects
        connect: 127.0.0.1:4001    # a local service
        # listen: 127.0.0.1:4001   # as an alternative: a local client
```

A port declaration has these keys for local addresses. The exporter does
not report them.

| Key | Direction | Meaning |
| --- | --- | --- |
| `listen` | `requires`, `connects` | The exporter opens a listener on this address for a local client. A `requires` port must have this key. |
| `connect` | `provides`, `connects` | The exporter connects to a local service on this address. On a `TcpNetwork` driver, the default is the `host` and the `port` of the driver. |

A `connects` port must have one of the two keys.

Each port also has two optional properties:

| Property | Values | Default | Rule |
| --- | --- | --- | --- |
| `attachment` | `socket`, `wired`, `wireless` | `socket` | A forward accepts only `socket` ports (DD-8). |
| `transport` | `stream` (TCP), `datagram` (UDP), `frame` (link-layer frames) | `stream` | A forward accepts only `stream` ports (DD-9). |

A driver can report the other values, and users can find those ports. For
example, a SOME/IP service discovery endpoint is a `connects` port with the
transport `datagram`.

Users find the port names in the exporter report:

```console
$ jmp get exporters -l device-type=aaos-headunit --with ports -o json | jq '.exporters[].ports'
[{"name":"rootcanal","direction":"provides","protocol":"hci-h4"}]
```

### Forwards

A forward gives a name to a set of endpoints. This template connects the
Bluetooth host of a peer to the HCI controller of the head unit:

```yaml
apiVersion: jumpstarter.dev/v1alpha1
kind: LeaseTemplate
metadata:
  name: headunit-peer
  namespace: jumpstarter-lab
spec:
  description: "Virtual head unit and a Bluetooth peer on its controller"
  members:
    - name: peer
      selector: { matchLabels: { device-type: bt-peer } }
    - name: headunit
      selector: { matchLabels: { device-type: aaos-headunit } }
  forwards:
    - name: bt
      endpoints:
        - { member: headunit, port: rootcanal }    # provides
        - { member: peer,     port: controller }   # requires
  defaults:
    duration: 45m
```

These rules apply:

- The endpoints can be in any sequence. The controller gets the direction
  of each port from the exporter reports (DD-6).
- Each endpoint is on a different member.
- A forward has one `provides` port and one or more `requires` ports. As an
  alternative, it has two `connects` ports.
- If two ports declare a `protocol`, the values must be the same (DD-5).
- A forward name obeys the rules of a role name. A forward and a role of
  one lease cannot have the same name.

**One `provides` port for many members.** Each `requires` port opens its
own connections to the service:

```yaml
  forwards:
    - name: console
      endpoints:
        - { member: dut,      port: console }   # provides
        - { member: logger,   port: console }   # requires
        - { member: debugger, port: console }   # requires
```

A `provides` port can also be in many forwards (DD-10). A `requires` port
has one listener, thus it can be in only one forward.

**A path.** When no side supplies a service, the two ports declare
`connects`. Here, two serial bridges on different benches connect as a
cross-over (DD-11):

```yaml
  forwards:
    - name: uart
      endpoints:
        - { member: dut,       port: uart }   # connects
        - { member: companion, port: uart }   # connects
```

**Optional members.** A forward can refer to an optional member that is
`Omitted`. The forward then serves its other endpoints, if they include the
`provides` port and a minimum of one `requires` port. If they do not, the
forward is `Disabled`. A path with one `Omitted` end is also `Disabled`.

A disabled forward does not prevent a `Ready` lease. The ports of the other
endpoints must exist in each case (*Forward validation*).

### How to use forwards

The CLI can add a forward to an ad-hoc lease:

```console
$ jmp create lease \
    --member peer=device-type=bt-peer \
    --member headunit=device-type=aaos-headunit \
    --forward bt=headunit.rootcanal,peer.controller \
    --duration 45m
lease-9m2qd
```

In a shell, the `j-forward` command shows the forwards of the lease (DD-12):

```console
jumpstarter ⚡ lease-9m2qd ➤ j-forward status
NAME   ENDPOINTS                                STATE
bt     headunit.rootcanal → peer.controller     Connected
```

An arrow points from the `provides` port to each `requires` port. The sign
`↔` connects the two ends of a path. The command reads the states from the
lease status.

In Python, an attribute with the annotation `Forward` is a forward of the
template:

```python
from jumpstarter.client.lease import Forward, LeaseMembers
from jumpstarter_driver_bt_peer.client import BtPeerClient
from jumpstarter_driver_composite.client import CompositeClient
from jumpstarter_driver_power.client import PowerClient


class Peer(CompositeClient):
    bt_peer: BtPeerClient


class Headunit(CompositeClient):
    power: PowerClient


class HeadunitPeer(LeaseMembers, template="headunit-peer"):
    peer: Peer
    headunit: Headunit
    bt: Forward


with config.lease(HeadunitPeer) as lease:      # config: as in JEP-0015
    with lease.connect() as members:
        members.headunit.power.on()
        members.peer.bt_peer.start_peer('{"name": "Bumble-Phone"}')
        members.peer.bt_peer.wait_connection(timeout=60)
```

- The forward name is the attribute name with `-` in the place of `_`.
  `forward(name=...)` sets a different name.
- The class does not contain endpoints. The template contains them.
- The check before the request (JEP-0015, DD-13) also compares the
  forwards. It raises `TypeError` if the class declares a forward that the
  template does not contain.
- A `Forward` object has the attributes `name` and `state`.
  `wait_listening(timeout)` returns when the forward is `Listening` or
  `Connected`. It raises an error with the cause if the forward is `Failed`
  or `Disabled`. A test can use it after an interruption.
- The attribute of a `Disabled` forward is a `Forward` object with that
  state.

The client waits for `Ready`, as JEP-0015 describes. A lease with a
`Failed` forward does not become `Ready`. Thus the client stops the wait
when a forward is `Failed`:

- In Python, the client raises an error with the forward and the cause.
- `jmp shell` shows the cause as a warning and opens the shell. The user
  can then examine the members (DD-7).

### How a forward starts

The exporter uses the current `TemporaryTcpListener` and `forward_stream()`
functions. The controller does this setup one time for each forward:

1. After it binds the lease, the controller validates the ports.
2. The controller sends a setup instruction to each endpoint exporter on
   its `Listen` stream. It sends the instruction each time that the
   exporter opens `Listen` for the lease. An exporter ignores a setup that
   it applied before.
3. The `requires` side opens its listener. The `provides` side records the
   forward. Each side reports the result with `ReportForward`.
4. When each bound endpoint is ready, the forward is `Listening`.

An exporter reads the instructions on `Listen` after its `beforeLease`
hook, as it does for client connections today. Thus a forward starts after
the hooks, and a hook cannot use a forward.

Each connection then gets its own router stream, as a client connection
does today (DD-2):

1. The driver on the `requires` side connects to the listener.
2. The exporter on the `requires` side calls `DialPeer`.
3. The controller creates a stream ID and signs a router token for it.
4. The controller sends the token to the `provides` exporter in a connect
   instruction on `Listen`. It then returns the same token to the caller.
5. The two exporters call `RouterService.Stream` with the token. The router
   connects them, as it connects a client and an exporter.
6. The `provides` side connects to its local service.
7. Each side connects its local connection to the stream with
   `forward_stream()`.

For a path, the initiator does the steps of the `requires` side, and the
other end gets the connect instruction (DD-11). These rules apply to each
connection:

- **Time limit:** If the peer does not join the stream in a time limit, the
  exporter closes the stream and the local connection. The proposal is 30
  seconds.
- **Half-close:** When a local connection closes one direction, the
  exporter sends that half-close through the stream, as `forward_stream()`
  does today.
- **Close:** When a local connection or the stream closes fully, the other
  parts of the connection also close.
- **Slow receiver:** The flow control of gRPC limits a sender.

```{mermaid}
sequenceDiagram
    participant D as Driver
    participant R as Exporter (requires)
    participant C as Controller
    participant X as RouterService
    participant P as Exporter (provides)
    participant S as Local service

    C->>R: ForwardSetup on Listen
    C->>P: ForwardSetup on Listen
    Note over R: Open the local listener
    D->>R: Connect to the listener
    R->>C: DialPeer
    C->>P: ForwardConnect with a router token
    C-->>R: The same token
    R->>X: Stream(token)
    P->>X: Stream(token)
    P->>S: Connect
    D->>S: Data through the two exporters and the router
```

### Reference topologies

Drivers supply the ports that forwards use. These are the ports of the
reference topologies and of some other bridges:

| Stack | Port | Direction | Note |
| --- | --- | --- | --- |
| rootcanal HCI (Cuttlefish) | `rootcanal` | `provides` | HCI on TCP. Hosts connect to it. |
| rootcanal link layer | `rootcanal-link` | `provides` | Accepts the link of a different rootcanal controller. Only for rootcanal without netsim. |
| rootcanal remote link | `rootcanal-remote` | `requires` | The local controller connects through it to the link layer of a different controller. |
| `jumpstarter-driver-bt-peer` | `controller` | `requires` | A Bumble host that connects to an external controller. |
| Projection server on a phone | `projection`, `projection-wifi` | `provides` | The same service through ADB or through the Wi-Fi address of the guest. |
| Projection receiver | `projection-server` | `requires` | Connects to the projection server of the phone. |
| `socketcand` or a CAN-over-TCP bridge | `can` | `provides` | A CAN segment as a socket. |
| `socketcand` client bridge | `can-remote` | `requires` | Connects a local CAN interface to a remote `socketcand`. |
| Serial bridge | `uart` | `connects` | A cross-over between two serial lines. |
| GPIO bridge | `gpio` | `provides` / `requires` | One side sends the level changes of a pin. The other side applies them. The timing is that of the network. The physical pin is a `wired` port (DD-8). |
| SOME/IP service discovery | `someip-sd` | `connects` | UDP multicast, transport `datagram`. Reported only (DD-9). |

**Virtual Bluetooth.** Two Cuttlefish exporters run in different Pods. Each
has its own rootcanal controller. A forward connects the two controllers at
the link layer (DD-13):

```yaml
  forwards:
    - name: bt-link
      endpoints:
        - { member: headunit, port: rootcanal-link }     # provides
        - { member: phone,    port: rootcanal-remote }   # requires
```

The rootcanal controller of the phone then gets a join command with the
`listen` address of its `rootcanal-remote` port (*Unresolved Questions*).
The two devices then find each other and pair. Bluetooth LE uses a second
forward of the same form.

**Virtual device and peer.** A Cuttlefish head unit supplies `rootcanal`.
A `bt-peer` exporter connects its Bumble host to it. This is the
`headunit-peer` template above.

**Virtual projection.** A Cuttlefish phone runs a phone image with the
projection application. A `projection-rx` driver runs a receiver process in
the head unit exporter. The receiver connects to the phone (DD-14):

```yaml
  forwards:
    - name: session
      endpoints:
        - { member: phone,    port: projection-wifi }     # provides
        - { member: headunit, port: projection-server }   # requires
```

A lab that supplies the ADB path and the Wi-Fi path publishes one template
for each path. The test selects a path when it selects a template.

**CAN across hosts.** Two ECUs use one CAN segment through `socketcand`:

```yaml
  forwards:
    - name: powertrain-bus
      endpoints:
        - { member: node,    port: can }          # provides
        - { member: gateway, port: can-remote }   # requires
```

A control driver is not a forward endpoint. For example,
`jumpstarter-driver-netsim` uses a REST API to set radios to on or off and
to collect pcap captures.

### API / Protocol Changes

All the changes add fields or RPCs. `RouterService` does not change.

**Driver report** (`jumpstarter.v1`). Ports are optional. An exporter that
reports no ports cannot be in a forward (DD-4). `PortReport` and its enums
are in `common.proto`. The client protocol imports that file today.

```protobuf
message DriverInstanceReport {
  // Fields 1 to 5 do not change. JEP-0011 and the native gRPC proposal
  // reserve fields 6 and 7.
  repeated PortReport ports = 8;   // NEW, optional
}

message PortReport {
  string name = 1;                 // A DNS label: "rootcanal", "controller"
  PortDirection direction = 2;
  optional string protocol = 3;    // Free text. Compared only if the two ends set it.
  PortAttachment attachment = 4;   // UNSPECIFIED means SOCKET (DD-8).
  PortTransport transport = 5;     // UNSPECIFIED means STREAM (DD-9).
}

enum PortDirection {
  PORT_DIRECTION_UNSPECIFIED = 0;
  PORT_DIRECTION_PROVIDES = 1;
  PORT_DIRECTION_REQUIRES = 2;
  PORT_DIRECTION_CONNECTS = 3;
}

enum PortAttachment {
  PORT_ATTACHMENT_UNSPECIFIED = 0;
  PORT_ATTACHMENT_SOCKET = 1;      // A local socket.
  PORT_ATTACHMENT_WIRED = 2;       // A cable or a bus. A forward cannot use it.
  PORT_ATTACHMENT_WIRELESS = 3;    // A radio medium. A forward cannot use it.
}

enum PortTransport {
  PORT_TRANSPORT_UNSPECIFIED = 0;
  PORT_TRANSPORT_STREAM = 1;       // A TCP byte stream.
  PORT_TRANSPORT_DATAGRAM = 2;     // UDP. A forward cannot use it.
  PORT_TRANSPORT_FRAME = 3;        // Link-layer frames. A forward cannot use it.
}
```

- A port name obeys the rules of a role name. It is a DNS label with a
  maximum of 63 characters.
- A port name is unique across all the driver reports of one exporter. The
  exporter makes sure of this before it registers. The controller rejects a
  registration with a duplicate name with `INVALID_ARGUMENT`.
- The controller also rejects a port that has no direction.
- Thus one member and one port name give one port. The exporter finds the
  port by its name. An instruction contains no driver UUID, because each
  lease session creates new driver instances.
- The report does not contain the `listen` address or the `connect`
  address of a port.

Each entry of `ExporterStatus.Devices[]` gets a `ports` list with the
fields of `PortReport`. The client message `jumpstarter.client.v1.Exporter`
gets `repeated jumpstarter.v1.PortReport ports = 8` as an output field.

**`Lease` and `LeaseTemplate`** each get an optional `forwards` list.
`LeaseStatus` gets the forward status.

```go
// LeaseSpec and LeaseTemplateSpec each get these two rules and one field.
// Forwards must have members. A forward and a member cannot have the same name.
// +kubebuilder:validation:XValidation:rule="!has(self.forwards) || (has(self.members) && self.forwards.all(f, f.endpoints.all(e, self.members.exists(m, m.name == e.member))))",message="forward endpoints must reference declared members"
// +kubebuilder:validation:XValidation:rule="!has(self.forwards) || !has(self.members) || self.forwards.all(f, !self.members.exists(m, m.name == f.name))",message="a forward and a member cannot have the same name"
type LeaseSpec struct {
    // ... the current fields and the JEP-0015 fields do not change ...

    // +listType=map
    // +listMapKey=name
    // +kubebuilder:validation:MaxItems=16
    Forwards []LeaseForward `json:"forwards,omitempty"`
}

type LeaseForward struct {
    // +kubebuilder:validation:MaxLength=63
    // +kubebuilder:validation:Pattern=`^[a-z0-9]([-a-z0-9]*[a-z0-9])?$`
    Name string `json:"name"`

    // The list key is the member, thus each endpoint is on a different member.
    // +listType=map
    // +listMapKey=member
    // +kubebuilder:validation:MinItems=2
    // +kubebuilder:validation:MaxItems=8
    Endpoints []ForwardEndpoint `json:"endpoints"`
}

type ForwardEndpoint struct {
    // +kubebuilder:validation:MaxLength=63
    Member string `json:"member"`
    // +kubebuilder:validation:MaxLength=63
    Port   string `json:"port"`
}

// LeaseStatus
Forwards []LeaseForwardStatus `json:"forwards,omitempty"`

type LeaseForwardStatus struct {
    Name    string `json:"name"`
    State   string `json:"state"` // Pending | Disabled | Listening | Connected | Failed
    Message string `json:"message,omitempty"`
}
```

`forwards` is immutable, as `members` is. The lease gets a new condition
type, `ForwardsReady`. Only a lease with forwards has it.

| `ForwardsReady` | Meaning |
| --- | --- |
| True | Each forward that is not `Disabled` is `Listening` or `Connected`. |
| False, reason `ForwardsPending` | A minimum of one forward is `Pending`, and none is `Failed`. |
| False, reason `Failed` | A minimum of one forward is `Failed`. |

| Forward state | Meaning |
| --- | --- |
| `Pending` | The controller did not complete the setup. |
| `Disabled` | An optional member is `Omitted`, and the forward cannot operate without it. The message gives the role. |
| `Listening` | The setup is complete. No connection is open. |
| `Connected` | A minimum of one connection is open. |
| `Failed` | The validation or the setup failed, or a necessary member is `Lost`. The message gives the cause. |

A forward has one state for all its endpoints. The controller calculates
it from the validation and from the reports of the endpoint exporters
(`ReportForward`):

- `Listening`: each bound endpoint reported that it is ready.
- `Connected`: a minimum of one endpoint reported an open connection.
- `Failed`: one endpoint reported a failure.

**Client protocol** (`jumpstarter.client.v1`):

```protobuf
enum LeaseForwardState {
  LEASE_FORWARD_STATE_UNSPECIFIED = 0;
  LEASE_FORWARD_STATE_PENDING = 1;
  LEASE_FORWARD_STATE_DISABLED = 2;
  LEASE_FORWARD_STATE_LISTENING = 3;
  LEASE_FORWARD_STATE_CONNECTED = 4;
  LEASE_FORWARD_STATE_FAILED = 5;
}

message ForwardEndpoint {
  string member_name = 1;
  string port_name = 2;
}

message LeaseForward {
  string name = 1;
  repeated ForwardEndpoint endpoints = 2;   // Two to eight, on different members.
}

message LeaseForwardStatus {
  string name = 1;
  LeaseForwardState state = 2;
  optional string message = 3;
}

message Lease {
  // Fields 1 to 22 are the current fields and the JEP-0015 fields.
  repeated LeaseForward forwards = 23 [(google.api.field_behavior) = IMMUTABLE];
  repeated LeaseForwardStatus forward_statuses = 24 [(google.api.field_behavior) = OUTPUT_ONLY];
}

message LeaseTemplate {
  // Fields 1 to 10 are the JEP-0015 fields.
  repeated LeaseForward forwards = 11;
}
```

`CreateLease` accepts `forwards` only in the members form of JEP-0015. If a
scalar request or a template request sets `forwards`, `CreateLease` returns
`INVALID_ARGUMENT` with the field name and creates nothing. Template
expansion copies the forwards of the template without changes.

**Controller protocol** (`jumpstarter.v1`):

```protobuf
// Listen is a server stream from the controller to an exporter.
message ListenResponse {
  string router_endpoint = 1;                // no change
  string router_token = 2;                   // no change
  oneof forward {                            // NEW; not set for a client connection
    ForwardSetup forward_setup = 3;          // One time for each forward.
    ForwardTeardown forward_teardown = 4;
    ForwardConnect forward_connect = 5;      // One time for each connection.
  }
}

message ForwardPeer {
  string member_name = 1;
  string port_name = 2;
}

// The controller can send the same setup again. The key is
// (lease_uid, forward_name, member_name).
message ForwardSetup {
  string lease_uid = 1;
  string forward_name = 2;
  string member_name = 3;
  // REQUIRES: the PROVIDES endpoint. PROVIDES: each bound REQUIRES
  // endpoint. CONNECTS: the other end.
  repeated ForwardPeer peers = 4;
  PortDirection direction = 5;      // The direction of the local port.
  string local_port_name = 6;
  bool initiator = 7;               // CONNECTS only: this end opens the path (DD-11).
}

// The exporter closes the listener and the streams of the forward.
message ForwardTeardown {
  string lease_uid = 1;
  string forward_name = 2;
}

// Tells the PROVIDES side, or the second end of a path, to open one connection.
message ForwardConnect {
  string lease_uid = 1;
  string forward_name = 2;
  string connection_id = 3;      // The stream ID.
  string peer_member_name = 4;   // The member that opens the connection.
  string router_endpoint = 5;
  string router_token = 6;       // The token that DialPeer returns.
}

service ControllerService {
  // ... the current RPCs do not change ...
  rpc DialPeer(DialPeerRequest) returns (DialPeerResponse);
  rpc ReportForward(ReportForwardRequest) returns (ReportForwardResponse);
}

// The REQUIRES side, or the initiator of a path, calls this RPC for each
// connection.
// The controller finds the member from the identity of the caller.
message DialPeerRequest {
  string lease_uid = 1;
  string forward_name = 2;
}

message DialPeerResponse {
  string router_endpoint = 1;
  string router_token = 2;     // `sub` is the new stream ID, as in Dial.
  string connection_id = 3;    // That stream ID.
}

enum ForwardEndState {
  FORWARD_END_STATE_UNSPECIFIED = 0;
  FORWARD_END_STATE_READY = 1;       // The listener is open, or the forward is recorded.
  FORWARD_END_STATE_CONNECTED = 2;   // A minimum of one connection is open.
  FORWARD_END_STATE_FAILED = 3;      // For example, the listen address is in use.
}

// An exporter reports each change of the state of its end of a forward.
// It does not report each connection.
message ReportForwardRequest {
  string lease_uid = 1;
  string forward_name = 2;
  ForwardEndState state = 3;
  optional string message = 4;       // The cause of a failure.
}

message ReportForwardResponse {}
```

- `DialPeer` operates as `Dial` does. The controller accepts the call only
  if all these conditions are true:
  - The lease is active.
  - The caller is the exporter of a `Bound` member of the lease.
  - That member is a `requires` endpoint of the forward or the initiator of
    a path.
  - The forward is `Listening` or `Connected`.
- The controller selects the router with the labels of the caller, as
  `Dial` does with its one exporter.
- If the exporter that must get `ForwardConnect` has no `Listen` stream,
  `DialPeer` fails with `UNAVAILABLE`. If the `Listen` queue of that
  exporter is full, `DialPeer` fails with `RESOURCE_EXHAUSTED`. The
  `requires` side then closes the local connection.
- A `ListenResponse` contains a client connection or one forward
  instruction, not the two together. An exporter rejects a response that
  has the two.
- An old exporter reports no ports. Thus it gets no forward instruction.

**CLI:**

- `jmp create lease` and `jmp shell` accept ad-hoc
  `--forward NAME=MEMBER.PORT,MEMBER.PORT`. More than two endpoints connect
  one `provides` port to many `requires` ports.
- `jmp get exporters --with ports` shows the declared ports.
- `jmp describe lease-template` shows the forwards of a template.
- `j-forward status` is a new command in `jmp shell`.

**Exporter configuration and driver API:**

- A driver instance in the exporter configuration gets the optional
  `ports` list (*Ports*).
- `Driver` gets one optional method. The exporter calls it when a forwarded
  stream of a port of that driver closes. Its name is an implementation
  detail.

### Hardware Considerations

- **Hardware:** Virtual devices must have hosts with KVM. They do not use
  physical radios.
- **Physical radios:** Physical devices use the air. They use no forward.
- **Physical cables and buses:** A forward can use a physical bus only
  through a socket bridge, such as `socketcand`. The bus timing is then
  that of the network.
- **Latency:** A forward adds the latency of the router. Measure Bluetooth
  HCI and audio with the intended workloads.
- **Listeners:** A `requires` port uses a fixed local address. Each
  exporter must have its own network namespace (*Deployment*).

## Design Decisions

This table is a summary of the decisions. Each subsection gives the
alternatives and the rationale.

| Decision | Result |
| --- | --- |
| DD-1 | Forwards use the router. A later JEP adds a direct path. |
| DD-2 | Each forwarded connection gets its own router stream. |
| DD-3 | Ports have a name and a direction. A lease contains no addresses. |
| DD-4 | Ports are an optional part of the exporter report. |
| DD-5 | A port has a direction and an optional protocol tag. |
| DD-6 | The controller infers the direction. The lease gives the topology. |
| DD-7 | The controller validates ports after the bind. |
| DD-8 | A forward accepts only socket ports. |
| DD-9 | A forward accepts only TCP streams. |
| DD-10 | One `provides` port can serve many `requires` ports. |
| DD-11 | Two `connects` ports make one path. |
| DD-12 | A `j-forward` command shows the forwards. |
| DD-13 | Simulated Bluetooth connects at the rootcanal link layer. |
| DD-14 | Projection uses an L4 forward. |

### DD-1: Forwards use the router

**Alternatives considered:**

1. **Router streams only.** Each forward goes through the router. A later
   JEP adds a direct path.
2. **Router streams with a direct path in this JEP.** Exporters in the same
   network zone connect to each other directly.
3. **A client relay.** The client moves the bytes between two streams.
4. **Direct connections only.**

**Decision:** Option 1.

**Rationale:** The router connects exporters that cannot connect to each
other, for example edge devices behind NAT. Option 4 does not serve them.
Option 3 makes the forward depend on the network and the lifetime of the
client.

A direct path decreases latency and router load. But it is the first
connection where no end is the controller. Thus each exporter must prove
its identity to the other exporter. That identity must be the one that all
of Jumpstarter uses, and a different JEP must define it. If this JEP
defines its own identity, an exporter gets two identities from two issuers.

The first version of JEP-0015 kept reserved fields for the direct path:
`mode`, `peerEndpoint`, `networkZone`, and the peer identity fields. This
JEP removes them. They did nothing, and reviewers read them as
available functions. The JEP that adds the direct path can add its fields,
because new fields are backward compatible.

A direct path must have these properties:

- **Mutual:** Each exporter proves its identity to the other.
- **Encrypted:** A person who reads the connection cannot read the data.
- **No bearer secret on the path:** A person who reads the connection gets
  nothing that they can use.
- **Authorization from the controller:** An identity gives no access. The
  `provides` side accepts a connection only if the controller announced it,
  and each connection ID is valid one time.
- **Opaque identities:** An exporter only compares the identity of its peer
  with the identity that the controller gave.
- **No routable name:** A peer can have only a Pod IP address.
- **Renewal without a restart.**

In a prototype on one node, a router forward had a median round-trip time
of 0.70 ms. A direct connection had 0.05 ms. A rootcanal HCI command took
approximately 45 ms on the two paths. These values do not show the limits
across nodes.

### DD-2: One router stream for each forwarded connection

**Alternatives considered:**

1. **A stream ID for each connection, from the controller.** The `requires`
   side asks the controller for each connection. The controller gives the
   two exporters a token, as `Dial` does for a client.
2. **One fixed subject for each forward**, with member and side claims that
   the router validates.
3. **A new peer RPC** in `router.proto`.

**Decision:** Option 1. `RouterService` does not change.

**Rationale:** The router connects two callers whose tokens have the same
`sub`. For a client connection, `Dial` creates a stream ID and gives one
token to the client and to the exporter. Option 1 uses the same procedure
with an exporter in the place of the client. Only the two exporters hold
the token. Thus the router code and its protobuf stay the same.

A forward can have many connections at the same time. With option 2:

- The router cannot identify each connection.
- The `provides` side gets no signal to open a stream.
- The token must stay valid for the full lease.

Option 3 adds a second stream path with no new check. The cost of option 1
is one controller call for each connection. New connections are possible
only when the controller is available, as client connections are today.

### DD-3: Named ports with a direction, not addresses

**Alternatives considered:**

1. **Named ports on the two ends**, each with a direction. The lease
   contains only names.
2. **Addresses in the lease**, for example `listen: 127.0.0.1:7300`.
3. **Named endpoints without a direction.**

**Decision:** Option 1.

**Rationale:** With names, the exporter configuration owns the addresses. A
lease can refer to `headunit.rootcanal` and does not contain a port number.

The direction lets the controller reject incorrect connections without
protocol knowledge. Example: a Bumble host can connect to an HCI
controller. Two HCI controllers cannot connect to each other in this way.
The controller rejects that forward, because the two ports declare
`provides`.

Each direction has one name. Synonyms such as `tx` and `rx` make reports
difficult to compare. They also suggest one-way data, but each forward is a
two-way stream.

### DD-4: Ports are an optional part of the exporter report

**Alternatives considered:**

1. **A new optional `ports` field** in `DriverInstanceReport`, with a copy
   in `ExporterStatus.Devices[]`.
2. **Ports as driver labels.** No proto change and no CRD change.
3. **No report.** Ports are only in the exporter configuration.

**Decision:** Option 1.

**Rationale:** With a report, users can find the ports, and the controller
can validate a forward before it starts. With option 3, errors occur only
at connection time. Option 2 gives a second function to driver labels. A
label is also one string, and a port has four properties.

An exporter without the field reports an empty list. Thus old exporters
continue to register and to serve leases. They cannot be in a forward.

### DD-5: A direction and an optional protocol tag

**Alternatives considered:**

1. **A `medium` list** (`bluetooth`, `wifi`, `serial`, `can`) that the two
   ends must match.
2. **A wire protocol token** that the two ends must match.
3. **The direction only**, with an optional free-text `protocol`. The
   controller compares it only when the two ends declare it.

**Decision:** Option 3.

**Rationale:** A medium name does not show wire compatibility. rootcanal
HCI and the netsim `PacketStreamer` are both Bluetooth, but they use
different protocols. A protocol token without a direction does not find a
connection between two HCI controllers. The project also must not keep a
central list of protocols.

If one end has no tag, the forward can connect incompatible protocols. The
error then occurs when the ends exchange data. A forward guarantees byte
transport. It does not guarantee protocol compatibility.

### DD-6: The controller infers the direction and not the topology

**Alternatives considered:**

1. **Infer the direction only.** A forward lists its endpoints in any
   sequence. The lease gives the ports that connect.
2. **Infer the topology also.** The controller connects each matching pair
   of ports. The lease has no `forwards` list.
3. **Infer nothing.** The author gives the source and the destinations.

**Decision:** Option 1.

**Rationale:** The exporter report contains the direction. With
option 3, the author writes it again, and the API has two shapes. But the
author must select the ports. A forward to `projection` and a forward to
`projection-wifi` test different paths.

With option 2, the connections depend on the ports of the exporters that
the controller selected. Many matches are ambiguous. Option 2 can also make
a data path that the test did not request.

### DD-7: The controller validates ports after the bind

**Alternatives considered:**

1. **Validation after the bind**, against the reports of the bound
   exporters.
2. **Validation at selection time.** Ports become exporter labels, and
   selectors use them.
3. **A candidate filter by port name.** The JEP-0015 assignment removes a
   candidate that does not report the ports that the forwards name.

**Decision:** Option 1.

**Rationale:** The current selectors use the labels of the `Exporter`
resource. The ports are in `ExporterStatus.Devices[]`. Option 2 must have
ports that selectors can read, and the selector code does not support
this.

Option 3 uses no labels, but it adds the driver reports to the JEP-0015
assignment. That assignment uses only labels and claims. Option 3 is a
possible later change (*Future Possibilities*).

The cost of option 1 is that a lease with an invalid forward keeps its
members until release or expiry. The user can examine them in that time.

### DD-8: A forward accepts only socket ports

**Alternatives considered:**

1. **Report the attachment, and forward only sockets.** A port reports
   `socket`, `wired`, or `wireless`.
2. **Connect physical media.** Add exporters that control relays, VLANs, or
   signal gateways.
3. **Do not report physical ports.**

**Decision:** Option 1.

**Rationale:** Option 2 must have devices that serve many exporters,
channel allocation for each lease, isolation at release, and electrical
checks. These are not a part of byte forwarding.

A physical CAN port and a `socketcand` bridge to it are different
connection points. A byte forward of the physical port loses the bus timing
without a warning. Thus the controller rejects it. With option 3, the
controller cannot see the difference.

The attachment does not say that a device is virtual. A physical ECU behind
`socketcand` has a `socket` port. A forward can connect it to the `socket`
port of a virtual device in the same lease (JEP-0015, DD-15). A forward
cannot connect a virtual device to a physical radio, because a radio is not
a socket (*Future Possibilities*).

### DD-9: A forward accepts only TCP streams

**Alternatives considered:**

1. **TCP streams only. Each port reports its transport.**
2. **UDP datagrams as frames on the router stream.**
3. **L2 segments in this JEP.**
4. **No transport in the report.**

**Decision:** Option 1.

**Rationale:** Many automotive protocols use UDP, and some use multicast on
a shared segment. SOME/IP service discovery is the primary example. ECUs
announce services by multicast, and each offer contains the IP address of
the service.

- Option 2 keeps the datagram limits but not the datagram behavior. On TCP,
  one lost packet delays all the subsequent packets. A forward also cannot
  change the addresses that discovery messages contain.
- Option 3 is the correct model for multicast. But it must have a datagram
  transport, network privileges in each exporter, a shared address plan,
  and isolation for each lease. It is a different design.
- With option 4, a later feature changes the meaning of old reports.

With option 1, a driver can describe a SOME/IP service discovery endpoint
today. A later JEP then permits such ports without a schema change. SOME/IP
methods and events on TCP operate today, if the configuration gives their
endpoints.

### DD-10: One `provides` port can serve many `requires` ports

**Alternatives considered:**

1. **Independent connections to one `provides` port.** A forward can list
   many `requires` ports. A `provides` port can be in many forwards.
2. **Only two endpoints for each port.**
3. **A forward that copies** the source bytes to each destination.

**Decision:** Option 1.

**Rationale:** Many services accept more than one client. A rootcanal HCI
port accepts many hosts. Each connection has its own stream (DD-2), thus a
second `requires` port is only one more listener.

With option 2, a lab must run one bridge for each user of the service.
Option 3 puts protocol behavior in the forward. Only the service knows if
its clients can share data.

### DD-11: Two `connects` ports make one path

**Alternatives considered:**

1. **A path between two `connects` ports.**
2. **Permit forwards between two `provides` ports or two `requires`
   ports.**
3. **Report `connects` ports only**, and add no path.
4. **Links with more than two members.**

**Decision:** Option 1.

**Rationale:** Some bench connections have no client and no server. A
serial cross-over is the usual example. Each bench has a serial-over-TCP
bridge that listens. No end supplies a service to the other.

- Option 2 removes the check of DD-3. A driver declares `connects` to show
  that the port can connect to its own type.
- Option 3 gives no correct description for the serial example.
- Option 4 is the segment model that DD-9 does not include.

A path operates as other forwards do, with these differences:

- **Initiator:** The controller selects the endpoint whose member name is
  first in sorted order. Only the initiator calls `DialPeer`. The other end
  gets `ForwardConnect`, as a `provides` exporter does.
- **Local mode:** Each exporter configures its `connects` port with
  `connect` or with `listen` (*Ports*). The exporter does not report this
  mode. An initiator that listens opens the path when its local client
  connects. If the other end listens and has no local client, it waits for
  one for the time limit of a connection.
- **One connection:** A path has one connection at a time. When it closes,
  the two ends close their local sides. The initiator opens the path again
  when its local side is ready. An initiator that connects to a local
  service tries again, with a delay that increases.
- **Same checks:** The two ports must be `socket` and `stream`. A
  `connects` port can be in only one forward.

### DD-12: A `j-forward` command

**Alternatives considered:**

1. **A `j-forward` command.**
2. **A `j forward` subcommand**, with `forward` and `forwards` as reserved
   role names.

**Decision:** Option 1.

**Rationale:** The names below `j` come from exporters and templates. They
are driver names in a scalar shell and role names in a member-form shell.
Option 2 puts reserved words in the API. It also conflicts with a driver
that has the name `forward`. A different command cannot conflict.

### DD-13: Simulated Bluetooth connects at the rootcanal link layer

**Alternatives considered:**

1. **One shared rootcanal.** The second Cuttlefish device uses the HCI
   service of the first device through a forward.
2. **Two rootcanal controllers connected at the link layer.** Each device
   keeps its controller. A forward connects the two controllers.
3. **A shared virtual controller from Bumble.**
4. **A shim in the Android guest.**

**Decision:** Option 2. Option 1 stays possible with the same port model.

**Rationale:** Cuttlefish uses TCP ports for rootcanal: HCI on `7300+N` and
the link layer on `7400+N`. Thus a byte forward is sufficient.

- With option 1, the forward must exist before the second device starts.
  The shared controller is also one point of failure.
- With option 2, each device keeps its own controller. It must have
  rootcanal without netsim, a `requires` port for the remote link, and a
  join command after the forward starts.
- Option 3 is possible later. Its performance for audio is not known.
- Option 4 changes the software that the test examines.

Manual tests in September 2026 used two Cuttlefish Pods on one node. The
devices completed discovery, pairing, HFP, A2DP, and AVRCP with options 1
and 2. The tests gave these requirements for the implementation:

- **Private test channel:** A fast connect and close on the rootcanal test
  port can stop rootcanal. Do not use connect probes for forward health.
- **Unique Bluetooth addresses:** rootcanal uses the same addresses again.
  Set the address of each member before the pair procedure.
- **Reconnection:** After a cut stream, the listener must stay open. The
  subsequent connection then gets a new stream.
- **One process for each exporter identity:** A second process with the
  same identity breaks driver sessions.

### DD-14: Projection uses an L4 forward

**Alternatives considered:**

1. **Forward the `mac80211_hwsim` frame socket**, thus the two devices use
   one simulated Wi-Fi medium.
2. **Connect at the netsim 802.11 chip.**
3. **Forward the projection TCP connection** and simulate no radio.

**Decision:** Option 3. Simulated Wi-Fi is not a part of this JEP.

**Rationale:** An L4 forward tests the version negotiation, TLS, service
discovery, video, audio, and input of the projection protocol. It does not
test the Bluetooth credential exchange, Wi-Fi association, or signal loss.
It gives a useful test before a simulated medium is available.

Cuttlefish connects `mac80211_hwsim` to `wmediumd` through vhost-user.
vhost-user uses shared memory. A byte stream cannot send that connection
across hosts. Thus option 1 must have a frame bridge on each exporter. Option 2
can use the netsim packet transport, but only where the netsim Wi-Fi
support is sufficient.

In a manual test, a phone image in one Pod connected to a desktop receiver
in a different Pod. The session completed the negotiation and showed the
launcher, first through ADB and then through the Wi-Fi address of the
guest.

## Design Details

### Deployment

- Each exporter must have its own network namespace. `requires` ports can
  then use fixed loopback addresses.
- Each exporter identity must have only one process. A second process with
  the same identity breaks driver sessions. This JEP does not change the
  registration (*Unresolved Questions*).
- A virtual exporter can run in its own Pod. A physical exporter can run on
  its own edge device or in an isolated container.
- Two exporters with host networking on one machine are not supported.
  Their listeners can conflict.
- A `requires` listener opens without `SO_REUSEPORT`
  (`TemporaryTcpListener(reuse_port=False)`). Thus an address that is in
  use causes a failure.

### Forward validation

The controller validates each forward after it binds the lease. It uses
`ExporterStatus.Devices[].Ports`. At admission, CEL makes sure that each
endpoint refers to a member of the lease.

1. Ignore the endpoints of `Omitted` members.
2. Each other port exists on its bound exporter.
3. If step 1 ignored an endpoint, examine the other endpoints. The forward
   is `Disabled` in these two conditions:
   - The other endpoints include `PROVIDES` or `REQUIRES` ports, but not
     one `PROVIDES` port together with a minimum of one `REQUIRES` port.
   - The forward lists two endpoints, and the other endpoint is `CONNECTS`.

   The controller does no more checks for a `Disabled` forward.
4. The direction is valid. One port is `PROVIDES`, and all the others are
   `REQUIRES`. As an alternative, the forward has two endpoints, and the
   two are `CONNECTS`.
5. Each port has the attachment `socket` and the transport `stream`.
6. All the `protocol` values that the ports declare are equal.
7. No `requires` port and no `connects` port is in two forwards. If one is,
   each forward that contains it fails. This check ignores `Disabled`
   forwards.

A failure sets the forward to `Failed`. It also sets `ForwardsReady=False`
and `Degraded` on the lease. The members stay bound. The controller does
not set `Invalid`, because `Invalid` ends the lease. In JEP-0015, only a
`Lost` member causes `Degraded`. This JEP adds a failed forward as a second
cause.

A lease with forwards has one more condition for `Ready`, in addition to
the JEP-0015 conditions. Each forward that is not `Disabled` must be
`Listening` or `Connected`. While a forward is `Pending`, `Ready` is false
with the reason `ForwardsPending`.

### Connections and interruptions

The controller issues the router token for each connection as `Dial` does.
The token has a random stream ID as `sub`. It expires after 60 seconds. A
`Dial` token expires after 30 minutes today. The expiry limits only the
time to open the stream.

One local TCP connection and one router stream make one pair. These rules
apply after an interruption:

- If the stream closes, the exporter closes that local connection. It does
  not attach a new stream to it.
- A router stream ends for the two peers when one goes away
  ([#1328](https://github.com/jumpstarter-dev/jumpstarter/pull/1328)).
- The `requires` side keeps its listener open. A driver that connects
  again gets a new stream through `DialPeer`.
- After a cut stream, the exporter calls the optional method of the driver
  of that port. Some protocols must start a server again or restore state.
- The forward state comes from the listener and its streams. The exporter
  does not use probes of the service.

Forward data runs in the current task group of each driver. Thus a forward
that stops does not block the driver calls of other children.

### End of a forward

- **Lease end:** An exporter closes all the listeners and streams of the
  forwards of a lease when that lease ends. It does this before its
  `afterLease` hook, with or without a `ForwardTeardown` instruction.
- **`Lost` member:** `DialPeer` refuses a member that is not `Bound`. The
  streams of that member close. If the forward cannot operate without that
  member, the forward is `Failed`, and the controller sends
  `ForwardTeardown` to its other endpoints. If the member is one of many
  `requires` endpoints, the forward continues for the other endpoints.
- **Token:** A token that the exporters did not use expires after 60
  seconds. Thus a token gives no access a long time after the lease ends.

### Failure modes

| Failure | Behavior |
| --- | --- |
| A port is not on the bound exporter | The forward is `Failed`, and the lease is `Degraded`. The members stay bound. |
| Two endpoints are `provides`, or all are `requires` | The forward is `Failed`, and the lease is `Degraded` (DD-3). |
| The declared protocols are different | The forward is `Failed`, and the lease is `Degraded` (DD-5). |
| One protocol tag is absent | Validation passes. Errors can occur when the ends exchange data. |
| A port is `wired`, `wireless`, `datagram`, or `frame` | The forward is `Failed`, and the lease is `Degraded` (DD-8, DD-9). |
| A forward has `connects` ports and other ports, or more than two `connects` ports | The forward is `Failed`, and the lease is `Degraded` (DD-11). |
| A `requires` port or a `connects` port is in two forwards | Each of those forwards is `Failed`, and the lease is `Degraded` (*Forward validation*). |
| A forward refers to a member that is not in the lease | CEL rejects the lease at admission. |
| An `Omitted` member leaves a forward without its `provides` port or without a `requires` port | The forward is `Disabled`. The lease can become `Ready`. |
| An `Omitted` member is one end of a path | The forward is `Disabled`. The lease can become `Ready` (*Forwards*). |
| An `Omitted` member is one of many `requires` endpoints | The forward serves the other endpoints. |
| One exporter reports the same port name two times, or a port without a direction | The controller rejects the registration with `INVALID_ARGUMENT`. |
| The `listen` address is in use | The forward is `Failed`, and the lease is `Degraded`. |
| The router stream closes | The local connection closes. The forward goes back to `Listening`. |
| The peer exporter has no `Listen` stream | `DialPeer` returns `UNAVAILABLE`. The `requires` side closes the local connection. |
| The `Listen` queue of the peer exporter is full | `DialPeer` returns `RESOURCE_EXHAUSTED`. The `requires` side closes the local connection. |
| The peer does not join the stream in the time limit | The exporter closes the stream and the local connection. |
| A member becomes `Lost` (JEP-0015, DD-8) | Its streams close, and the lease is `Degraded`. A forward that cannot operate without the member is `Failed` (*End of a forward*). |
| The lease ends | Each exporter closes its listeners and streams before its `afterLease` hook. |

### Reference drivers

- **`cuttlefish` (phone):** Prepares the image with ADB and the necessary
  Bluetooth profiles. Starts the projection server on request and starts it
  again after a cut stream.
- **`projection-rx` (head unit):** Runs the receiver with a screen and a
  dummy audio device. Starts the receiver on a client call, after the
  forward listener is available.
- **`bt-peer`:** Gets a `profiles` list for HFP and A2DP. Keeps the bond
  keys for the lease lifetime.

The drivers configure only local endpoints. The lease gives the connection
between them.

### Security

- **Authorization:** `DialPeer` makes sure that the caller is the exporter
  of a `Bound` member of an active lease. The member must be a `requires`
  endpoint of the forward or the initiator of a path.
- **Tokens:** The controller issues one token for each connection. Only the
  two exporters get it. It expires after 60 seconds.
- **Encryption:** Each exporter opens `RouterService.Stream` on the TLS
  channel that it uses for client streams today.
- **Ports:** A forward can use only declared ports. A `requires` listener
  uses an address from the exporter configuration. It exists only while
  the exporter serves a lease.
- **Authentication:** A forward uses the current token authentication of
  the router. This JEP adds no new authentication.
- **Immutable field:** `forwards` cannot change after creation.
- **Simulator control:** rootcanal listens on `0.0.0.0` without client
  authentication. Keep its test channel private. A network policy must
  block direct access to the simulator ports.

### Observability

For forwards, the exporter telemetry records these values:

- The bytes in each direction.
- The number of connections.
- The number of cut streams.

The forward name is a correlation field of JEP-0013, as `lease_member` is
in JEP-0015. It is not a metric label. The forward status shows the state
and the cause of a failure. `jumpstarter-driver-netsim` can collect pcap
captures of a simulated medium for the test results.

## Test Plan

### Unit Tests

- **CEL rules:** Forward names are unique, and no forward has the name of a
  role. A forward has two to eight endpoints on different members.
  Endpoints refer only to members of the lease. `forwards` is immutable.
- **Request forms:** `CreateLease` rejects `forwards` in a scalar request
  and in a template request with `INVALID_ARGUMENT`. CEL rejects `forwards`
  without `members`.
- **Port reports:**
  - Declared ports go to `ExporterStatus.Devices[].Ports` with direction,
    attachment, and transport.
  - An exporter that reports no ports registers as it does today.
  - A duplicate port name or a port without a direction rejects the
    registration.
- **Forward validation:** Each row of *Failure modes* gives its result. A
  failed forward does not end the lease. A `Disabled` forward does not
  prevent `Ready`.
- **`DialPeer`:**
  - The caller must be the exporter of a `requires` endpoint or the
    initiator of a path.
  - The call fails for a member that is not `Bound`, for a lease that
    ended, and for a forward that is `Failed` or `Disabled`.
  - Each call creates a new stream ID and sends the same token to the
    `provides` exporter.
  - Two connections on one forward at the same time get different streams.
  - The call fails with `UNAVAILABLE` when the `provides` exporter has no
    `Listen` stream.
  - The current router tests pass without changes.
- **`ReportForward`:** The forward state follows the reports of its
  endpoints. One failure report sets the forward to `Failed`.
- **Paths:** Two `connects` ports make a path for each combination of local
  modes. Only the initiator calls `DialPeer`. A path opens again after a
  cut. A path with one `Omitted` end is `Disabled`.
- **End of a forward:** An exporter closes its listeners and streams when
  the lease ends, without a teardown instruction. A `Lost` member that the
  forward must have sets the forward to `Failed`.
- **Many `requires` ports:** A forward with three endpoints serves the two
  `requires` ports.
- **`Listen`:** The exporter decodes setup, teardown, and connect
  instructions. It ignores a setup that it applied before. Each exporter of
  one lease gets only its own instructions. An exporter that reports no
  ports gets no forward instruction.
- **Python client:** A `Forward` attribute gives a `Forward` object. The
  check before the request rejects a forward that the template does not
  contain. The client stops the wait for `Ready` when a forward is
  `Failed`.

### Integration Tests

These tests use a kind cluster with the controller and mock exporters
(`e2e/`):

- **Router forward:** Two mock exporters and one template with one forward
  from an `EchoNetwork` port. The forward goes through the router. The test
  makes sure that the bytes are correct. Many connections at the same time
  each get their own data.
- **Router only:** No exporter opens a direct connection to a different
  exporter. All the forward data goes through `RouterService`.
- **Cleanup:** Expiry and release leave no router streams and no
  listeners.
- **CLI:** Ad-hoc `--forward`, `jmp get exporters --with ports`, and
  `j-forward status`.
- **Compatibility:**
  - An N-1 exporter reports no ports and registers with an N controller.
  - An N client sends `--forward` to a controller without this JEP. The
    client shows an error, and no lease stays.

### Hardware-in-the-Loop Tests

- **Virtual Bluetooth:** Two Cuttlefish exporters in different Pods pair
  through forwarded rootcanal ports. The test runs on CI nodes with KVM.
- **Virtual device and peer:** A Cuttlefish exporter connects to `bt-peer`
  and pairs. With HFP, a simulated call gets to the head unit.
- **Virtual projection:** A phone device and `projection-rx` in different
  Pods connect through `projection-wifi`. The receiver shows the launcher.
- **Latency:** Publish the HCI round-trip times through a forward and for
  a connection on one host without a forward. Measure across nodes and
  with A2DP.
- **Interruption:** Restart the router while a stream is open. The local
  connection closes, and a later connection gets a new stream.

### Manual Verification

- A user who did not see the exporter configuration writes a correct
  forward from `jmp get exporters --with ports`.
- `j-forward status` shows each forward and its state.

## Acceptance Criteria

**Ports**

- [ ] `PortReport` is optional in `DriverInstanceReport`. Exporters that
      report no ports operate as they do today.
- [ ] Port names are unique in one exporter. A duplicate name rejects the
      registration.
- [ ] Declared ports are in `ExporterStatus.Devices[].Ports` and in
      `jmp get exporters --with ports`.
- [ ] A local address of a port is not in a report, a CR status, or a
      lease spec.

**Forwards**

- [ ] `Lease` and `LeaseTemplate` have `forwards`, with the CEL rules.
- [ ] `CreateLease` rejects `forwards` in a scalar request and in a
      template request.
- [ ] Forward validation rejects absent ports, invalid directions, ports
      that are not `socket` and `stream`, and different protocols. It does
      not end the lease.
- [ ] A `Disabled` forward gets no token and does not prevent `Ready`.
- [ ] `ListenResponse` contains setup, teardown, and connect instructions.
- [ ] Exporters report the state of their forward ends with
      `ReportForward`, and the forward status shows the result.
- [ ] An exporter closes the listeners and streams of a lease when that
      lease ends. A `Lost` member gets no new stream.
- [ ] Each forwarded connection gets its own stream ID from `DialPeer`.
      `RouterService` does not change (DD-2).
- [ ] All forwards use the router. There is no direct path and no transport
      mode (DD-1).
- [ ] One `provides` port serves many `requires` ports (DD-10).
- [ ] Two `connects` ports make one path for each combination of local
      modes (DD-11).
- [ ] `bt-peer` is a `requires` endpoint through exporter configuration
      only.
- [ ] After a cut stream, the endpoint closes the local connection. A later
      connection gets a new stream.
- [ ] `j-forward status` shows the forwards. A Python `Forward` attribute
      gives a `Forward` object.

**Topologies**

- [ ] **Virtual Bluetooth:** Two Cuttlefish devices in Pods on different
      nodes complete discovery and the pair procedure, in CI.
- [ ] **Virtual projection:** A phone device and `projection-rx` in
      different Pods show the projection launcher through a forward, in CI
      without lab hardware.
- [ ] The project publishes the HCI latency of router forwards and the
      supported workload limits.

## Backward Compatibility

All the schema changes and protocol changes add items.

- **CRD:** `Lease` and `LeaseTemplate` get the optional `forwards` list.
  `LeaseStatus` gets `forwards`. `ExporterStatus.Devices[]` gets `ports`.
- **Driver report:** `ports` is a new optional field. An old exporter
  reports none and cannot be in a forward. A port with no `attachment` is a
  `socket` port. `bt-peer` does not change its Python code.
- **Protocol:** There are new fields in current messages and two new
  `ControllerService` RPCs. An old exporter gets no forward instruction,
  because it reports no ports.
- **N client, controller without this JEP:** A controller that has JEP-0015
  but not this JEP ignores `forwards` in a members request. It creates the
  lease without them. Thus the client compares `forwards` in the
  `CreateLease` response with its request. If they are different, it
  deletes the lease and shows a message that the controller is too old.
- **N exporter, controller without this JEP:** The controller ignores the
  reported ports. The exporter gets no forward instruction and opens no
  listener.
- **Exporter configuration:** `ports` is a new optional key. A
  configuration without it does not change.
- **Router:** `RouterService` and its token authentication do not change.
- **Operator upgrade:** The upgrade adds CRD fields. A conversion webhook
  is not necessary.
- **Rollback:** Remove the leases with forwards before a rollback.

## Consequences

### Positive

- Exporters on different hosts can exchange data in one lease.
- A forward ends when its lease ends.
- Forwards use the current stream functions and a router that does not
  change.
- Users find the ports in the exporter reports.
- The port model serves Bluetooth, projection, CAN, and serial connections
  without protocol code in the controller.
- The driver code of `bt-peer` does not change.

### Negative

- Port declarations add configuration.
- Fixed listeners depend on a network namespace for each exporter.
- The controller validates ports after the bind. Thus a lease with an
  invalid forward holds its devices.
- An absent protocol tag lets a compatibility error occur at run time.
- All forwards go through the router. They add router latency and router
  load (DD-1).
- Each forwarded connection costs one controller call (DD-2).
- Protocols that find peers through UDP multicast cannot operate across
  exporters (DD-9).

### Risks

- **Latency:** The results from one node do not show the performance across
  nodes or with continuous audio. Publish measurements for each workload.
- **Simulator failures:** The rootcanal test channel can stop rootcanal.
  Keep the control ports private (DD-13).
- **Stream interruption:** An ingress reload or a router restart can cut a
  long connection. Tests must include a planned stream loss.
- **Deployment:** A shared network namespace can cause listener conflicts.
  Enforce the rules in *Deployment*.
- **Projection artifacts:** The lab must supply the phone image, the
  application, and the receiver. CI must have a source for them.
- **Upstream interfaces:** The rootcanal and netsim interfaces can change.
  Use fixed versions of the runtime images.

## Rejected Alternatives

The Design Decisions give the alternatives for each decision. These are
the higher-level alternatives:

- **Let each test make its own connections.** The test must then know lab
  addresses. The connections also do not end with the lease.
- **A client relay.** The data path then depends on the client (DD-1).
- **Automatic connections between all matching ports.** This can make data
  paths that the test did not request (DD-6).
- **Physical link control in this JEP.** It must have shared infrastructure
  exporters and channel allocation (DD-8).
- **One JEP for leases and port forwards.** The first version of JEP-0015
  did this. The one document was too large to review.

## Prior Art

- **Cuttlefish multi-device connectivity** supplies the simulator
  interfaces of DD-13 and DD-14.
- **Android emulator networking** is a model for device connections on one
  host.
- **Bumble** supplies virtual hosts, controllers, and link relays. The
  `jumpstarter-driver-bt-peer` driver shows the `requires` port model.
- **`kubectl port-forward`** gives the same guarantee: byte transport
  without a protocol check.

## Unresolved Questions

For the review:

- **Link-layer setup:** What does the rootcanal join and the address
  assignment: a driver hook, a lease action, or the test?
- **Start sequence:** A forward starts after the `beforeLease` hooks. How
  does a device start that must use a shared HCI controller (DD-13, option
  1) in its hook?
- **Duplicate exporter processes:** A second process with an identity that
  is in use breaks driver sessions. Must the controller reject it? A
  rejection must have an instance ID in `RegisterRequest`, and it changes
  the registration for all exporters.

For the implementation:

- The reconnect behavior for each protocol, for example a restart of the
  projection server.
- A limit for the connections of one forward and for the `DialPeer` rate.
- How CI gets the projection artifacts.
- A test of the Bumble virtual controller with Cuttlefish (DD-13).
- The token change that is necessary if a later JEP binds router tokens to
  exporter identities.

## Future Possibilities

These items are not part of this JEP:

- **Direct path:** Exporters in one network zone connect to each other
  without the router. A different JEP must first define the exporter
  identity (DD-1).
- **Datagram forwards:** Send UDP between `datagram` ports and frames
  between `frame` ports on a transport that is not TCP.
- **Network segments:** Connect the `connects` ports of more than two
  members into one L2 segment. The first use is SOME/IP service discovery.
- **Simulated Wi-Fi:** Connect two virtual devices through a frame bridge
  for `mac80211_hwsim` or through netsim. This lets a test examine the
  change from Bluetooth to Wi-Fi in a projection session.
- **Shared infrastructure exporters:** Exporters such as switches, relay
  matrices, and signal gateways that serve many leases. They let forwards
  use `wired` and `wireless` ports (DD-8).
- **Virtual device with a physical radio:** A gateway exporter owns a radio
  adapter and supplies it as a `provides` port.
- **Port validation at selection time:** Remove the candidates that do not
  report the necessary ports, or supply the ports as dynamic exporter
  labels. A request that cannot be satisfied then holds no exporters
  (DD-7).
- **Dynamic listeners:** The exporter selects a free address for each
  `requires` port.
- **Vehicle bus simulation:** A restbus simulator as a `provides` port.

## Implementation History

- 2026-09-01: The first version of JEP-0015 proposed leases and forwards
  in one document
  ([#1069](https://github.com/jumpstarter-dev/jumpstarter/pull/1069)).
- 2026-11-23: The forwards became this JEP. The direct path and simulated
  Wi-Fi moved to *Future Possibilities*.

## References

- [JEP-0015: Multi-Exporter Leases](JEP-0015-multi-exporter-leases.md)
- [JEP-0014: Virtual Scalable Exporters](JEP-0014-virtual-scalable-exporters.md)
- [JEP-0013: Metrics, Tracing, and Log Observability](JEP-0013-observability-telemetry-logs.md)
- [JEP-0011: Protobuf Introspection and Interface Generation](JEP-0011-protobuf-introspection-interface-generation.md)
- Native gRPC Services (draft, not submitted). It reserves
  `DriverInstanceReport` field 7.
- [jumpstarter-dev/jumpstarter#986](https://github.com/jumpstarter-dev/jumpstarter/pull/986)
  — `jumpstarter-driver-bt-peer`
- [jumpstarter-dev/jumpstarter#980](https://github.com/jumpstarter-dev/jumpstarter/pull/980)
  — `jumpstarter-driver-netsim`
- [jumpstarter-dev/jumpstarter#1328](https://github.com/jumpstarter-dev/jumpstarter/pull/1328)
  — a router stream ends for the two peers when one goes away
- [Cuttlefish: test connectivity of multiple devices](https://source.android.com/docs/devices/cuttlefish/connectivity)
- [Bumble, a Python Bluetooth stack](https://google.github.io/bumble/)
- [netsim (`platform/tools/netsim`)](https://android.googlesource.com/platform/tools/netsim/)
- [google/android-cuttlefish](https://github.com/google/android-cuttlefish)

---

*This JEP is licensed under the
[Apache License, Version 2.0](https://www.apache.org/licenses/LICENSE-2.0),
consistent with the Jumpstarter project.*
