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
| `RootcanalController` | The controller simulator of a virtual device (rootcanal): its HCI port, for another virtual device to use | `bluetooth`, `provides`, `hci-h4` | The HCI of the other device's own Bluetooth stack |
| `RemoteController` | The Bluetooth of a virtual device that has no simulator of its own | `bluetooth`, `requires`, `hci-h4` | The same |
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
own, and happen twice, once in each radio: each device pairs with the half in its own radio. What crosses the
path is everything above the link: L2CAP, and so SDP, RFCOMM and the audio and phone book profiles.

What a half does beside its radio, each for a reason that was found the hard way:

- It is discoverable only once the far half has said whose name to carry.
- A device that calls the half is passed on at once: it looks the half up (SDP, in the clear) and encrypts the
  link itself before it uses anything that needs it. When the *far* device speaks first, the half calls its own
  device, pairs and encrypts, and only then passes on what was said; until then it is held. Only a link the
  half opened is authenticated by the half, and only once.
- No link may sleep, and the devices may swap who leads a link: an idle Android asks for sniff mode, a virtual
  radio answers that badly, and a minute later calls the link dead.
- Secure Connections are off: there is then no key derived across transports to disagree about.
- One conversation: when one device hangs up, the other is hung up on. When a half is stopped, or the path
  is cut, both devices are hung up on. The devices' bonds stay.
- Who opened a link is what the radio says (the half leads a link it opened), not what the half was doing: a
  device that calls while the half is calling it has opened its own link, and is not kept waiting for the half
  to encrypt it.
- A call to a device is given up after `call_timeout` seconds, and a device that did not answer is not called
  again at once (2, 4, 8 … 30 seconds): a device whose Bluetooth is restarting must not keep the other waiting,
  nor be called while it restarts.
- The keys of the pairings done in a radio are kept for as long as the lease's session, so a device that
  paired can reconnect after its radio or the far half came back.
- A device that was made to forget its pairings (a factory reset of its Bluetooth, "Forget" in its settings)
  refuses a link the half authenticates with the key it kept. The half then forgets the key too, hangs up and
  calls once more, and pairs anew. Only that answer of the device is taken so. A link that cannot be made
  secure at all is hung up, not left standing.

### Pairing

There are **two links and two pairings**: device A with the half in A's radio, and the half in B's radio with
device B. A bridge above HCI cannot make one pairing span both: the exchange that makes a link key happens
inside each controller, between the two ends of one link. The halves make the two behave as one:

- **Both devices ask at the same time.** When a device opens a link to its half (a person picked the other
  device in its settings, or a paired device reconnects), the far half calls the far device at once and
  authenticates that link. If the two have never paired, both show their pairing dialog.
- **Both have to agree.** The half of the device that started does not agree to its pairing until the far
  device has agreed to its own. If the far device's person says no, does not answer within `pair_timeout`
  seconds, or the far device cannot be reached, the pairing of the device that started fails too, and nothing
  is kept on either side. If the person who started says no, the far device is hung up on and the key made in
  its radio is forgotten.
- **Both starting at once** is two devices each pairing with its own half: neither half calls, neither waits.
- **Paired devices reconnect from either side** without a dialog: each link is authenticated with its key.
- **"Forget" on one device** leads to a new pairing the next time, from either side. Only the device that
  forgot is asked again: the other still has its key, and its link is authenticated with it. (Two real
  devices would both ask.)

What this cannot do:

- **The six-digit codes are per link.** Each device shows the code of its own pairing with its own half. In
  two rootcanal radios both show the same constant; with a real controller on one side the two differ. A
  person comparing the two screens sees two codes that need not match, and each is nonetheless the right one
  for its device. Nobody compares a code *with the bridge*: the half agrees when the far device has agreed.
- The half cannot see a dialog: it knows that its device is pairing, and how that ended.
- Where the called device asks is the device's own business. An Android that is called shows the pairing
  dialog only while its Bluetooth settings are in front (or it was discoverable a moment ago); otherwise it
  posts a "Pairing request" notification, as it does for any device that calls it. A suite that wants the
  dialog opens the Bluetooth settings ("Pair new device") on the device that is *not* starting, too.
- An Android that pairs from its settings may pair and hang up, and come back a second or two later for
  what it wants of the other device: both devices are then hung up on once, and reconnect without being asked.
- A device that only looks the other one up (SDP, without pairing) still makes the far half call and
  authenticate the far device, which then asks its person to pair.

Every change of a half's state is logged at `INFO` with both devices' names: `idle`, `calling`, `linked`,
`pairing`, `secure`, and why.

### The port

`bluetooth`, `connects`, protocol `bt-l2cap-bridge`. The driver is the port's local side itself: there is no
socket, and the port has no `listen` or `connect` address. The path carries a stream of records, each a 4-byte
big-endian length and that many bytes (the framing of the Wi-Fi frame port). A record's first byte says what
it is:

| Kind | Name | The rest of the record |
| --- | --- | --- |
| 1 | `HELLO` | JSON: `{"version": 2, "name": …, "address": …, "class_of_device": …}`, the device beside the sender's radio. Sent when the path opens, when the half is started and when the identity changes |
| 2 | `L2CAP` | A 2-byte big-endian channel id, then one L2CAP PDU the sender's device said |
| 3 | `HANGUP` | Nothing: the sender's link to its device is gone |
| 4 | `BYE` | Nothing: the sender is being stopped |
| 5 | `LINK` | JSON: `{"by": "device" \| "bridge"}`: the sender has a link to its device, and who opened it |
| 6 | `PAIRING` | JSON: `{"state": "started" \| "complete" \| "failed" \| "unavailable", "reason": …}`: where the sender is with making its link secure. `unavailable`: it cannot call its device (no address) |

What is not a record closes the path; the exporters open it again. A kind that is not known is ignored. A half
that says version 1 knows kinds 1 to 4 only: it is passed on to as before, and its pairing is not waited for.

### Identity

A half has to say what the device of its own radio is on the air: its Bluetooth **name**, its **class of
device** and its own **address**. The far half carries the name and the class, so that is what the other
device lists; the address is where this half calls the device, and is reported by `peer()` on the far side.

**Asked of the device itself.** With `identity_from`, the driver asks the device, and goes on asking:

```yaml
config:
  identity_from:
    adb: 127.0.0.1:6520 # the Android device, as the exporter's ADB server knows it
```

It runs `adb shell` on the exporter (the ADB server of the exporter's `AdbDevice` driver, port 15037, unless
`server_port` says otherwise) and reads the name and state from `dumpsys bluetooth_manager`, the address from
`settings --user 0 get secure bluetooth_address` and the class from the `bluetooth.device.class_of_device`
property. It starts with the lease, before the device is even powered on, and asks every `interval` seconds
(10) and again after a call to the device failed: a device that boots, is renamed, or has its Bluetooth
switched off and on is followed without a test. A change is said to the far half, which takes the new name in
its radio without leaving it: a link that is up stays up. While the device says its Bluetooth is off, it is
not called.

With that on both exporters, each device lists the other under its real name, and either can start a pairing,
with no test code.

**From the configuration.** `name`, `class_of_device` and `address` are what is said until the device has
answered, and all there is without `identity_from`. Without an address a half cannot call its device, and
learns the address the first time the device calls.

**Over the radio.** For a physical device beside a real controller there is nothing to ask but the device, on
the air: with `learn_name` (the default of `ControllerBluetooth`), a device that has linked is asked its name
(an HCI remote name request), and the far half carries that instead of the configured one.

**Said by a test.** `set_identity` says it outright. What is said so stays said, whatever the device is found
to say later:

```python
phone.bluetooth.set_identity(name="The phone under test")
```

A persona cannot carry the far device's *address*: in a radio, the half has the address of its own controller.

### Configuration

```yaml
export:
  bluetooth:
    type: jumpstarter_driver_radio.bluetooth.RootcanalBluetooth
    config:
      name: Cuttlefish phone # until the device has said its own
      class_of_device: 0x5A020C
      identity_from:
        adb: 127.0.0.1:6520
```

| Parameter | Description | Type | Default |
| --- | --- | --- | --- |
| `name` | The Bluetooth name of the device of this radio | str | `Jumpstarter device` |
| `address` | The device's own Bluetooth address. Empty: learned when the device calls | str | `""` |
| `class_of_device` | The device's class of device (`0x5A020C` a phone, `0x200408` a car's audio) | int | `0x001F00` |
| `identity_from` | Where to ask the device who it is: `{adb: <serial or host:port>}`, with `server_port` (15037), `adb_path` (`adb`), `interval` (10) and `timeout` (15) | dict | |
| `learn_name` | Ask a device that has linked for its name over the radio | bool | `false` (`true` for `ControllerBluetooth`) |
| `call_timeout` | Seconds a call to the device may take | float | `8.0` |
| `pair_timeout` | Seconds the device that started a pairing is kept waiting for the other to agree | float | `25.0` |
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

`status()` adds:

| Field | Meaning |
| --- | --- |
| `on_air`, `persona` | The half is in the radio, and the name it carries there |
| `state` | `off`, `idle`, `calling`, `linked`, `pairing` or `secure`: the half's link to its device |
| `linked`, `calling`, `pairing`, `encrypted` | The same as flags; `opened_by` is `device` or `bridge` |
| `bonded` | A key is kept here for the device |
| `paired` | Both devices have a secure link to the bridge: the conversation is whole |
| `far` | What the far half says of itself: `{"link": …, "pairing": …, "stopped": …}`. `stopped`: it is on the path, and its radio was stopped |
| `last_error` | Why the last call or pairing failed; empty once a link is good again |
| `call_backoff` | Seconds before the device is called again after a call that failed |
| `device_up`, `identity_source` | What the identity source says: the device's Bluetooth is on; whether it answers |
| `heard`, `sent`, `held`, `dropped`, `calls`, `pairings` | Counts: what the device said and was passed on, what it was told, … |
| `error` | Trouble with the radio's HCI port or the path |

### Usage

With a lease whose forward joins the two `bluetooth` ports, there is nothing to do first: pair from either
device's own settings, as a person would. What a test may want besides:

```python
phone = members.phone.bluetooth
phone.wait_until_joined()  # returns once the far half is on the path (the radio is started already)
phone.peer()  # BluetoothIdentity(name="Head Unit", ...): whose name is carried here
# ... pair from either device's own settings, and agree on both ...
phone.wait_until_paired(timeout=60)  # both devices have a secure link to the bridge
phone.status()  # {"state": "secure", "paired": True, "bonded": True, "heard": 212, "sent": 198, ...}
# ... forget the pairing on a device, restart its Bluetooth ...
phone.wait_until_idle()  # nothing is in flight: no call, no pairing
phone.stop()  # out of each other's reach; phone.start() brings it back
```

`stop` takes one radio off the air; the forward stays `Connected`, because the path between the two exporters
is still there. `wait_until_joined` starts only the radio it is called on: after both were stopped, start both
(`phone.start()`, `headunit.start()`) before waiting on either. A half whose far half is stopped says so in
`status()["far"]["stopped"]`, and in the error of a `wait_until_joined` that times out.

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

## RootcanalController and RemoteController

Two virtual devices on **one** simulated air, with no bridge between them. rootcanal takes any number of hosts
on its HCI port and gives each a controller and an address of its own. A second Cuttlefish device that is made
without a simulator, and whose HCI goes to the first device's simulator, is simply a second device in the same
room: the two Android stacks pair with each other, once, and each shows the other's real name and the same
six-digit code. Bluetooth LE works as well as BR/EDR, because nothing in the path knows what is said.

```text
 head unit's exporter                                        phone's exporter
 Android - rootcanal (HCI on 7300) - RootcanalController === RemoteController - tcp_connector - Android
                                     provides bluetooth   |  requires bluetooth  (dials 7301)
                                                          |
                                         the forward: one TCP stream, H4
```

Use this where both devices are virtual and one can be made without a simulator. `RootcanalBluetooth` (the
bridge) is for the rest: a physical device, or two devices that each keep their own simulator.

### The device with the simulator

```yaml
export:
  bluetooth:
    type: jumpstarter_driver_radio.direct.RootcanalController
    # config:
    #   port: 7300                   # --hci_port: 7300 + instance_num - 1
```

Nothing runs: the driver declares the port, and the exporter connects to the simulator for each connection a
forward brings. `status` says how many hosts the forward has brought (`hosts`, `served`).

### The device without one

The device must be made so that it starts no simulator and dials another instance's. For Cuttlefish that is
`netsim_bt: false`, `netsim_uwb: false` and the launch flag `rootcanal_instance_num: 2`; the Cuttlefish
provisioner of an ExporterSet does all three with `parameters.bluetooth_controller: remote`. The device's
connector (`tcp_connector`) then dials `127.0.0.1:7301`.

```yaml
export:
  bluetooth:
    type: jumpstarter_driver_radio.direct.RemoteController
    config:
      adb: 127.0.0.1:6520            # switch the device's Bluetooth off and on for a new controller
      # guest: 127.0.0.1:7301        # where the device's connector dials
      # listen: 127.0.0.1:7311       # where the exporter listens for the forward
      # adb_server_port: 15037       # the ADB server of the `AdbDevice` driver
      # adb_path: adb
      # retry: 1.0
      # autostart: true
```

The driver stands between the connector and the forward, and it is there for three reasons:

- **The connector does not survive a cut stream.** It looks for its port once a second until it is there, but
  when its stream goes away its next write kills it, and Cuttlefish stops the whole device. The driver keeps
  the connector's connection for as long as the device runs, whatever the forward does.
- **A cut can fall in the middle of a packet.** The driver passes whole H4 packets only.
- **A path that comes back is a new controller.** It knows nothing of what the stack set up in the old one.
  With `adb` set, the driver switches the device's Bluetooth off and on, as a person would. Bonds are the
  stack's own, and stay. A stack that only ever talked to nobody (the far device was not powered on yet) is
  only switched on, in case it gave up.

A connection to the forward counts as a path once the controller at its far end has answered: the driver asks
it its address first (`controller` in `status`). The exporter takes a connection before it knows whether the
far device is powered on.

`stop` leaves the far controller (the device's Bluetooth hears nothing, and the device stays up); `start`
looks for it again.

### Joining them

```yaml
forwards:
  - name: bluetooth
    endpoints:
      - { member: phone, port: bluetooth }     # requires (RemoteController)
      - { member: headunit, port: bluetooth }  # provides (RootcanalController)
```

```console
$ jmp shell --lease-template phone-headunit-direct
$ j headunit cuttlefish power on     # first: Android on the phone does not finish booting without a controller
$ j phone cuttlefish power on
$ j phone bluetooth status           # peer: true, controller: the phone's address in the head unit's simulator
```

### What it does not do

- It does not work with a physical device, or with a device that keeps its own simulator: use the bridge.
- One device of the lease must have the simulator and be powered on first. When that device is powered off,
  the other loses its controller, and gets a new one (and its Bluetooth restarted) when it is back.
- The addresses are the simulator's: it gives its hosts consecutive ones, in the order they connect.

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
virtual. `exporter-cuttlefish-phone-direct.yaml` and `exporter-cuttlefish-headunit-direct.yaml` are the two
devices of the direct path, with the lease template `phone-headunit-direct`.
