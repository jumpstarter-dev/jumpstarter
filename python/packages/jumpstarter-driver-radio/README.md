# Radio driver

`jumpstarter-driver-radio` is for a device's radios: Wi-Fi and Bluetooth, simulated or real. A radio driver
gives one radio as a **port**. A forward of a lease joins the ports of two devices, and they are on the same
air, wherever their exporters run. The test is not in the path.

It is to radios what `jumpstarter-driver-power` is to power: one interface, and a driver for each kind of
radio behind it. The ports are those of JEP-0016 (multi-exporter port forwarding): each driver declares its
port, the exporter reports it at registration, and a lease or a lease template names it in a forward.

| Driver | Radio | Port | What crosses it |
| --- | --- | --- | --- |
| `WmediumdWifi` | The Wi-Fi of a virtual device whose medium is [wmediumd](https://github.com/bcopeland/wmediumd) (Cuttlefish, other `mac80211_hwsim` setups) | `hwsim`, `connects`, `ieee80211-frames` | 802.11 frames |
| `GatewayWifi` | One TCP port on a real test Wi-Fi network beside a physical device (an edge gateway) | `wifi-service`, `requires` | One TCP connection for each connection a device on the Wi-Fi makes |
| `RootcanalBluetooth` | The Bluetooth of a virtual device whose radio is rootcanal (Cuttlefish, netsim): its HCI port | `bluetooth`, `connects`, `bt-l2cap-bridge` | L2CAP, between two halves of a bridge |
| `ControllerBluetooth` | The Bluetooth of a physical device, through a real controller beside it: the controller's HCI port | `bluetooth`, `connects`, `bt-l2cap-bridge` | The same |
| `MockRadio` | None: what goes into its port comes back out | `echo`, `connects` (or `provides`, `requires`) | Anything |

## Installation

```{code-block} console
:substitutions:
$ pip3 install --extra-index-url {{index_url}} jumpstarter-driver-radio
```

## The interface

```{eval-rst}
.. autoclass:: jumpstarter_driver_radio.driver.RadioInterface()
    :members:
```

`status()` has at least `running`, `attached` (the local end has the radio) and `peer` (a far end is joined).

A radio starts by itself with the exporter's session for a lease and stops at the end of it (`autostart`,
true by default). A lease whose forwards join two radios therefore has the bridges set up before a test does
anything: a forward that is `Listening` or `Connected` has a running radio at each end. `start` and `stop`
remain, to take a radio off the air and put it back; with `autostart: false` nothing runs until `start`.

A radio driver declares its port by itself: an exporter configuration needs no `ports` key for it. Give one to
rename the port (two radios of one kind on one exporter need two names) or to change a local address:

```yaml
export:
  wifi:
    type: jumpstarter_driver_radio.wmediumd.WmediumdWifi
    ports:
      - name: hwsim-front
        direction: connects
        protocol: ieee80211-frames
```

Two things are every radio driver's own business, because no network between exporters can do them:

- whatever has a deadline: a simulator's acknowledgement, a radio's timing, a pairing;
- telling the far side's traffic from this side's, so that nothing goes round in a circle.

That is why the ports that put two radios on one air are `connects` ports: neither side is the service. Each
driver runs its half of a bridge beside its own radio, and the forward between the two halves is a path
(JEP-0016, DD-11). A port's `protocol` says what crosses it; the controller refuses a forward between two
ports that say different things.

## Joining radios: the forwards of a lease

The radios are joined by the lease, not by the test. A lease template names the members and the forwards
(`examples/lease-templates.yaml` has this one and two more):

```yaml
apiVersion: jumpstarter.dev/v1alpha1
kind: LeaseTemplate
metadata:
  name: phone-headunit
spec:
  members:
    - name: phone
      selector: { matchLabels: { type: phone } }
    - name: headunit
      selector: { matchLabels: { type: head-unit } }
  forwards:
    - name: wifi
      endpoints:
        - { member: phone,    port: hwsim }
        - { member: headunit, port: hwsim }
    - name: bluetooth
      endpoints:
        - { member: phone,    port: bluetooth }
        - { member: headunit, port: bluetooth }
```

or, for a lease made by hand:

```console
$ jmp create lease --member phone=type=phone --member headunit=type=head-unit \
    --forward wifi=phone.hwsim,headunit.hwsim --forward bluetooth=phone.bluetooth,headunit.bluetooth
```

That is all of the set-up: publish the template, lease it, and use the devices. Their radios are joined when
the lease is ready, so a suite goes straight to what a person would do, for example pairing from a device's
own Bluetooth settings.

```console
$ kubectl apply -f examples/lease-templates.yaml
$ jmp shell --lease-template phone-headunit
$ j-forward status
$ j phone bluetooth status
$ j phone wifi status
```

A test that wants to be sure of the radios before it goes on can wait for them; it does not have to:

```python
members.phone.wifi.wait_until_joined()  # the path between the two Wi-Fi bridges is open
members.phone.bluetooth.wait_until_joined()  # the far Bluetooth half is there, and this one is on the air
```

## WmediumdWifi

### How it works

```text
 exporter A                                                       exporter B
 ┌──────────────────────────────┐                                 ┌──────────────────────────────┐
 │ guest ── wmediumd ── bridge ─┼─ hwsim ═══ forward ═══ hwsim ───┼─ bridge ── wmediumd ── guest │
 └──────────────────────────────┘        (a byte stream)          └──────────────────────────────┘
```

The driver runs a small bridge beside the medium. The bridge joins the medium through its API socket as one
station that no device uses. What the medium's stations send to the air comes out of the frame port; what goes
into the frame port is sent into the medium by that station, which also takes on the addresses of the far
side's stations, so that answers to them come back to the bridge.

Two things decide where the bridge runs:

- wmediumd does nothing after it has delivered a frame to a client until the client acknowledges it. The
  bridge therefore acknowledges at once and locally, and only then passes the frame on. A bridge on the far
  side of a network would slow the whole medium to the speed of that network.
- The API socket is a unix socket. Where it is in another container of the Pod (as with the Cuttlefish
  provisioner), the bridge is run there through `jumpstarter-exec`, and its frame port is on the Pod's loopback.

The bridge is one file of standard-library Python, so that it runs with whatever Python the medium's container
has.

### The port

`hwsim`, `connects`, protocol `ieee80211-frames`. The exporter connects to the bridge's frame port (`host`,
`port`) for the forward; it does so only once the far end has joined the path, and waits for a bridge that is
not started yet, so a forward may be there from the start of the lease.

The frame port carries a stream of records. Each is a 4-byte big-endian length followed by that many bytes:
the framing of QEMU's `stream` and `socket` network devices. A Wi-Fi record's bytes are:

| Bytes | Meaning |
| --- | --- |
| 4, big-endian | The frequency the frame was sent on, in MHz |
| the rest | One 802.11 frame, without FCS |

One connection at a time: a new one replaces the one before it. A record that cannot be a frame closes the
connection. A peer that does not read loses frames, as a radio out of range would: it never holds the medium
up, and a frame that has waited two seconds is no longer sent.

### Configuration

```yaml
export:
  wifi:
    type: jumpstarter_driver_radio.wmediumd.WmediumdWifi
    config:
      port: 7310
```

| Parameter | Description | Type | Default |
| --- | --- | --- | --- |
| `api_socket` | The medium's API socket, a glob. The bridge looks for it until it is there, and again after the device is power cycled | str | `/tmp/cf_env_*/env-*/wmediumd_api_server` |
| `host`, `port` | Where the bridge's frame port listens | str, int | `127.0.0.1`, `7310` |
| `station` | A station of the medium's configuration that no device uses; the bridge sends as it | str | `02:15:b3:00:00:00` |
| `ignore` | Senders that are not passed on, as address prefixes | list | `["02:00:00:00", "02:15:b2"]` |
| `launcher_socket` | Run the bridge through `jumpstarter-exec` over this launcher socket. Empty runs it in the exporter's own container | str | `$JUMPSTARTER_LAUNCHER_SOCKET` |
| `python` | The Python of the container the bridge runs in | str | `python3` |
| `retry` | Seconds between looks for a bridge that is not up, while a path waits for it | float | `1.0` |
| `autostart` | Start the bridge with the lease's session | bool | `true` |

The defaults are Cuttlefish's. Its devices all have the same built-in access point (`02:00:00:00:…`) and the
same factory addresses (`02:15:b2:…`), which must not meet on one air; the randomized addresses Android uses
for scanning, for connecting and for its own access point do not clash, and are what crosses the bridge.

### What it does not do

- It does not model distance or loss between the two sides: each medium acknowledges a frame for the far side
  as it would for a station in range.
- It keeps the frame port open across a power cycle of the device but closes the connection, since the far
  side has to learn of the new medium. The path is then opened again by the exporters.

## GatewayWifi

A physical device has a real Wi-Fi radio. An edge gateway beside it (a Raspberry Pi, say) runs an access point
for a test network and offers **one TCP port** on it. A device on the Wi-Fi that connects to that port is
talking to whatever is attached behind the gateway. With this driver, that is a service of another member of
the lease: a web server inside a virtual head unit, for example.

```text
 tablet ))) test Wi-Fi ── gateway :8080 ─ attach :7410 ─ GatewayWifi ─ wifi-service ═══ forward ═══ web ── service
            └─────────────────── the host beside the tablet ───────────────────┘ (requires)      (provides)
```

### Which side is which

The driver's port, `wifi-service`, is **`requires`**; the service's port on the other member is `provides`.

- Connections start on this side: a device on the Wi-Fi connects, and the service it wants is on the other
  member. That is what `requires` means. One connection of a device is one connection of the forward, and
  each gets a stream of its own.
- The gateway does not dial. Its attach port listens, and expects whoever answers its Wi-Fi port to keep
  connections *waiting* there; it hands one to each device that connects. For a `requires` port the exporter
  listens too (at the port's `listen` address). Two listeners do not meet by themselves: the driver keeps
  `waiting` connections open to the attach port, and when a device speaks on one, connects it to the
  exporter's listener and opens another to wait in its place.

The other member offers the service with any driver that has a `provides` port, for example a `TcpNetwork`:

```yaml
export:
  web:
    type: jumpstarter_driver_network.driver.TcpNetwork
    config: { host: 127.0.0.1, port: 8000 }
    ports:
      - name: web
        direction: provides
```

It joins at the level of a connection: the device reaches one service, and the two are not on one network.
The device has to speak first on a connection (as an HTTP client does): that is how the gateway's waiting
connections learn that one of them is in use.

### Configuration

```yaml
export:
  wifi:
    type: jumpstarter_driver_radio.gateway.GatewayWifi
    config:
      attach: 127.0.0.1:7410
```

| Parameter | Description | Type | Default |
| --- | --- | --- | --- |
| `attach` | The gateway's attach port | str | `127.0.0.1:7410` |
| `listen` | Where the exporter listens for the forward: the `listen` address of the driver's port | str | `127.0.0.1:7411` |
| `waiting` | Connections kept waiting at the gateway: as many devices can connect at the same moment | int | `3` |
| `retry` | Seconds before a gateway that could not be reached is tried again | float | `2.0` |
| `autostart` | Attach to the gateway with the lease's session | bool | `true` |

`status()` adds `waiting`, `active` (connections joined right now), `joined` (those there have been) and
`refused` (a device connected while no forward had the port: its connection was closed).

## RootcanalBluetooth and ControllerBluetooth

### How it works

Two HCI ports cannot be joined by a byte stream: an HCI port is a controller, and a controller wants a host.
What joins two radios is a bridge that stands in both at once. In each radio it is a device that carries the
*other* device's name and class, and what a device says to it, it says to the other device:

```text
 phone ))) [ "Head Unit" | half ] ── bluetooth ═══ forward ═══ bluetooth ── [ half | "Phone" ] ((( head unit
           └ a host on the phone's HCI port ┘                               └ a host on the head unit's HCI port ┘
```

Each driver is one **half** of that bridge, beside its own radio: a Bluetooth host
([bumble](https://google.github.io/bumble/)) on the radio's HCI port. A forward between the `bluetooth` ports
of two radios is the path between the two halves. Each device finds the other by its name when it looks for
devices to pair with.

Everything with a deadline stays beside the radio. The link, its pairing and its encryption are each half's
own, and happen twice, once in each radio: each device pairs with the half in its own radio, which accepts
(and each shows the code of its own pairing). What crosses the path is everything above the link: L2CAP, and
so SDP, RFCOMM and the audio and phone book profiles.

What a half does beside its radio, each for a reason that was found the hard way:

- It is discoverable only once the far half has said whose name to carry.
- A device that calls the half is passed on at once: it looks the half up (SDP, in the clear) and encrypts the
  link itself before it uses anything that needs it. When the *far* device speaks first, the half calls its own
  device, pairs and encrypts, and only then passes on what was said; until then it is held. Only a link the
  half opened is authenticated by the half, and only once.
- No link may sleep, and the devices may swap who leads a link: an idle Android asks for sniff mode, a virtual
  radio answers that badly, and a minute later calls the link dead.
- Secure Connections are off: there is then no key derived across transports to disagree about.
- One conversation: when one device hangs up, the other is hung up on, unless its link is still being paired.
  When a half is stopped, or the path is cut, both devices are hung up on. The devices' bonds stay.
- The keys of the pairings done in a radio are kept for as long as the lease's session, so a device that
  paired can reconnect after its radio or the far half came back.

### The port

`bluetooth`, `connects`, protocol `bt-l2cap-bridge`. The driver is the port's local side itself: there is no
socket, and the port has no `listen` or `connect` address. The path carries a stream of records, each a 4-byte
big-endian length and that many bytes (the framing of the Wi-Fi frame port). A record's first byte says what
it is:

| Kind | Name | The rest of the record |
| --- | --- | --- |
| 1 | `HELLO` | JSON: `{"version": 1, "name": …, "address": …, "class_of_device": …}`, the device beside the sender's radio. Sent when the path opens, when the half is started and when the identity changes |
| 2 | `L2CAP` | A 2-byte big-endian channel id, then one L2CAP PDU the sender's device said |
| 3 | `HANGUP` | Nothing: the sender's device hung up its link |
| 4 | `BYE` | Nothing: the sender is being stopped |

What is not a record of this kind closes the path; the exporters open it again.

### Identity

A half has to be told what the device of its own radio is on the air: its Bluetooth **name**, its **class of
device** and its own **address**. The far half carries the name and the class; the address is where this half
calls the device, and is reported to a test by `peer()` on the far side.

They come from the exporter's configuration, so that the bridge is whole without a test:

- `name` and `class_of_device` are what the other device shows when it looks for devices to pair with.
- `address` is needed for a device that is *called*: when the far device is the one a person pairs from, this
  half has to call its own device, and it can only call an address. Without one the half learns the address
  the first time its device calls, and until then what the far device says is held. So give the address of
  every device that is not always the one to start.

The driver cannot ask its radio who the device is. On the HCI port it is a controller of its own, with its own
address and name; the device beside it is another station on the air, which answers a search only while its
own pairing screen is open. A test that has asked the device (over ADB, say) may still say what it found:

```python
phone.bluetooth.set_identity(name="Pixel 9", address="DA:4C:10:DE:17:00", class_of_device=0x5A020C)
```

A persona cannot carry the far device's *address*: in a radio, the half has the address of its own controller.

### Configuration

```yaml
export:
  bluetooth:
    type: jumpstarter_driver_radio.bluetooth.RootcanalBluetooth
    config:
      name: Cuttlefish phone
      class_of_device: 0x5A020C
      address: "DA:4C:10:DE:17:00"
```

| Parameter | Description | Type | Default |
| --- | --- | --- | --- |
| `name` | The Bluetooth name of the device of this radio | str | `Jumpstarter device` |
| `address` | The device's own Bluetooth address. Empty: learned when the device calls | str | `""` |
| `class_of_device` | The device's class of device (`0x5A020C` a phone, `0x200408` a car's audio) | int | `0x001F00` |
| `host`, `port` | The radio's HCI port, H4 over TCP | str, int | `127.0.0.1`, `7300` |
| `transport` | Any bumble transport instead of `host` and `port`, for example `hci-socket:0` | str | |
| `retry` | Seconds before a radio that is not there, or went away, is looked for again | float | `2.0` (`5.0` for `ControllerBluetooth`) |
| `autostart` | Take the HCI port and run the half with the lease's session | bool | `true` |

`RootcanalBluetooth` is for a virtual device: Cuttlefish's `--hci_port` is `7300 + instance_num - 1`. The half
joins the virtual radio as one more controller. Rootcanal gives every instance the same device address unless
it is told otherwise: two devices that are to pair need two.

`ControllerBluetooth` is for a physical device, through a real controller beside it: the HCI port an edge
gateway offers, or the controller itself (`transport: hci-socket:0`, which needs `CAP_NET_ADMIN` and a
controller that nothing else uses). The half is on real air under the far device's name. A gateway that
serves one host at a time is held from `start` to `stop`.

`status()` adds `on_air` (the half is in the radio under the far device's name), `persona` (that name),
`linked` and `encrypted` (its link to the device of this radio), `heard` (what that device said and was passed
on), `sent` (what it was told), `held`, `dropped` and `error`.

### Usage

With a lease whose forward joins the two `bluetooth` ports, there is nothing to do first: pair from either
device's own settings, as a person would. What a test may want besides:

```python
phone = members.phone.bluetooth
phone.wait_until_joined()  # returns once the far half is on the path (the radio is started already)
phone.peer()  # BluetoothIdentity(name="Head Unit", ...): whose name is carried here
# ... pair from either device's own settings ...
phone.status()  # {"linked": True, "encrypted": True, "heard": 212, "sent": 198, ...}
phone.stop()  # out of each other's reach; phone.start() brings it back
```

```console
$ j phone bluetooth status
$ j phone bluetooth peer
$ j phone bluetooth identity --name "Pixel 9" --address DA:4C:10:DE:17:00
$ j phone bluetooth stop
$ j phone bluetooth start
```

### What it does not do

- Bluetooth LE: the bridge is BR/EDR only.
- SCO: a call's audio does not cross (it is not L2CAP). A2DP, AVRCP, HFP's control channel and the phone book
  profiles do.
- It is not rootcanal's link layer (JEP-0016, DD-13): two virtual devices joined there are on one simulated
  air with their own addresses, where that is available. This bridge needs nothing of the simulator but its
  HCI port, and is the same for a real controller.

## MockRadio

A radio with nothing on the air, for trying a forward without a device. What goes into its port comes back
out, and `exchange` says something into the port and gives back the answer.

```yaml
export:
  radio:
    type: jumpstarter_driver_radio.driver.MockRadio
    # config:
    #   direction: requires          # connects (the default), provides or requires
    #   listen: 127.0.0.1:7500       # requires only: where the exporter listens for the forward
```

| `direction` | What it is | With a forward to |
| --- | --- | --- |
| `connects` | One end of a path | Another `connects` mock: `exchange` on either gives back what was said |
| `provides` | A service that echoes | A `requires` mock |
| `requires` | The client of one: `exchange` dials the `listen` address | A `provides` mock |

A mock radio is off until it is started:

```console
$ j a radio start
$ j b radio start
$ j a radio exchange hello
hello
```

## Examples

`examples/` has an exporter configuration for a Cuttlefish phone, a Cuttlefish head unit and an edge gateway
beside a tablet, and lease templates that join them: virtual to virtual (Wi-Fi and Bluetooth) and physical to
virtual.
