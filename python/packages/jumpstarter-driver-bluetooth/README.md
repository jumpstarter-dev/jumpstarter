# Bluetooth driver

`jumpstarter-driver-bluetooth` is for a device's Bluetooth, whatever stands behind it: a virtual radio or a
real controller, Linux, iOS or Android. A suite says `phone.bluetooth` and gets the same client with the same
methods; what differs between devices is the exporter's configuration.

A device's Bluetooth has up to two parts here, and an exporter names them in one entry:

- a **link**: how the device's radio is joined to another device of the lease. A link gives the radio as a
  **port** (JEP-0016, multi-exporter port forwarding); a forward of the lease joins the ports of two devices,
  wherever their exporters run. The test is not in the path.
- a **device view**: what the device's own Bluetooth stack says and does: its adapter, its bonds, on and off,
  and a recording of its HCI.

| Role | Driver | In the one entry | Port | What it is |
| --- | --- | --- | --- | --- |
| Link | `BluetoothBridge` | `link: bridge` | `bluetooth`, `connects`, `bt-l2cap-bridge` | One half of a bridge between two radios that do not share the air: a virtual radio's HCI port (rootcanal: Cuttlefish, netsim) or a real controller beside a physical device |
| Link | `BluetoothControllerPort` | `link: controller-port` | `bluetooth`, `provides`, `hci-h4` | A controller that takes several hosts (a virtual device's simulator), offered to another device |
| Link | `BluetoothRemoteController` | `link: remote-controller` | `bluetooth`, `requires`, `hci-h4` | A virtual device with no controller of its own uses that of another |
| Device view | `AndroidBluetooth` | `device: {adb: …}` | none | An Android device's own stack, over ADB |
| Both | `Bluetooth` | | the link's | The one entry: a link and a device view behind one client |
| | `MockBluetooth` | | `echo`, `connects` (or `provides`, `requires`) | Nothing: what goes into its port comes back out |

The port a link declares is always called `bluetooth`, so a lease template does not change with the link.

Not here yet:

- **Device views for iOS and Linux** are planned. A physical iPad today has a link only (a bridge on a real
  controller); its own stack is not asked.
- **The peer role**, a scripted device that a device under test pairs with and plays media to, is
  [`jumpstarter-driver-bt-peer`](bt-peer.md). It takes the same `transport`. It is proposed to join this
  package later, with its class name kept as an alias.

## Installation

```{code-block} console
:substitutions:
$ pip3 install --extra-index-url {{index_url}} jumpstarter-driver-bluetooth
```

## One entry for a device

```yaml
export:
  bluetooth:
    type: jumpstarter_driver_bluetooth.driver.Bluetooth
    config:
      transport: tcp-client:127.0.0.1:7300   # where the radio's HCI port is
      link: bridge                           # bridge | controller-port | remote-controller | none
      device:                                # or left out: nothing asks the device's own stack
        adb: 127.0.0.1:6520
        adb_path: /opt/android-sdk/platform-tools/adb
      name: Cuttlefish phone                 # the options of the link that was chosen
      class_of_device: 0x5A020C
```

| Parameter | Description | Default |
| --- | --- | --- |
| `link` | Which link driver this is: `bridge`, `controller-port`, `remote-controller`, or `none` | `none` |
| `transport` | The radio's HCI port as a [bumble transport](https://google.github.io/bumble/transports/index.html): `tcp-client:127.0.0.1:7300` (a virtual radio's HCI port, or a gateway's), `hci-socket:0`, `usb:0`, `serial:/dev/ttyUSB0`. `host` and `port` are short for the first kind | `tcp-client:127.0.0.1:7300` |
| `device` | How the device's own stack is asked: `{adb: <serial or host:port>}`, with `adb_path` (`adb`), `server_port` (15037, the ADB server of the exporter's `AdbDevice` driver), `timeout` (30), `snoop_log` and `poll` | none |
| the rest | The options of the chosen link, as in its section below. One that is not that link's is refused | the link's own |

`Bluetooth` is one driver, not a tree of them: it builds the link and the device view, declares the link's
port as its own, and passes each call to the part that can do it. A bridge and a remote controller ask the
device (who it is; switch its Bluetooth off and on) over the ADB of `device`, unless they are told otherwise.
Each driver of the table is also usable by itself, with the same client: `type:
jumpstarter_driver_bluetooth.driver.BluetoothBridge`, and so on.

## The interface

```{eval-rst}
.. autoclass:: jumpstarter_driver_bluetooth.interface.BluetoothInterface()
    :members:
```

```{eval-rst}
.. autoclass:: jumpstarter_driver_bluetooth.client.BluetoothClient()
    :members:
```

Every driver has every method. `get_bluetooth_state()` says what a driver can do, in `capabilities`, and what
is known of the device:

```python
phone.bluetooth.get_bluetooth_state()
# {"capabilities": ["link", "identity", "pairing", "state", "enable", "bonds", "capture", "packets"],
#  "adapter": {"enabled": True, "state": "ON", "name": "Pixel 9", "address": "3C:5A:B4:01:02:03",
#              "class_of_device": 5898764, "connection_state": "CONNECTED", "android_user": 10, ...},
#  "connections": None,          # the stack says that it has a link, not to whom
#  "bonds": [{"name": "Head Unit", "address": "XX:XX:XX:XX:17:01", "type": "BR/EDR", "class_of_device": 2098184}],
#  "link": {"running": True, "attached": True, "peer": True, "state": "secure", ...}}
```

What a driver cannot know is `None`, not made up. A method of a capability the driver has not raises
`NotImplementedError` with what would provide it:

```text
'capture' is not supported by this driver (BluetoothBridge): a device view (AndroidBluetooth, or Bluetooth
with 'device'): the device's own HCI snoop log
```

| Capability | Methods | Who has it |
| --- | --- | --- |
| `link` | `start`, `stop`, `status`, `ports`, `wait_until_attached`, `wait_until_joined` | The links |
| `identity` | `identity`, `set_identity`, `peer` | `bridge` |
| `pairing` | `wait_until_paired`, `wait_until_idle` | `bridge` |
| `state` | `get_bluetooth_state` asks the device itself | `device` |
| `enable` | `set_enabled` | `device` |
| `bonds` | `factory_reset` | `device` |
| `capture` | `start_capture`, `stop_capture`, `capture_status`, `read_capture` | `device` |
| `packets` | `read_packets`, `wait_for_event`, `should_not_have_event` | `device` |

`status()` has at least `running`, `attached` (the local end has the radio) and `peer` (a far end is joined).

A link starts by itself with the exporter's session for a lease and stops at the end of it (`autostart`, true
by default). A lease whose forwards join two devices therefore has the links set up before a test does
anything: a forward that is `Listening` or `Connected` has a running link at each end. `start` and `stop`
remain, to take a radio off the air and put it back; with `autostart: false` nothing runs until `start`.

A link declares its port by itself: an exporter configuration needs no `ports` key for it. Give one to rename
the port (two Bluetooth radios on one exporter need two names) or to change a local address.

Two things are every link's own business, because no network between exporters can do them:

- whatever has a deadline: a radio's timing, a pairing;
- telling the far side's traffic from this side's, so that nothing goes round in a circle.

## Joining two devices: the forwards of a lease

The radios are joined by the lease, not by the test. A lease template names the members and the forwards
(`examples/lease-templates.yaml` has this one, with the devices' Wi-Fi joined too, and three more):

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
    - name: bluetooth
      endpoints:
        - { member: phone,    port: bluetooth }
        - { member: headunit, port: bluetooth }
```

or, for a lease made by hand:

```console
$ jmp create lease --member phone=type=phone --member headunit=type=head-unit \
    --forward bluetooth=phone.bluetooth,headunit.bluetooth
```

That is all of the set-up: publish the template, lease it, and use the devices. Their radios are joined when
the lease is ready, so a suite goes straight to what a person would do, for example pairing from a device's
own Bluetooth settings.

```console
$ kubectl apply -f examples/lease-templates.yaml
$ jmp shell --lease-template phone-headunit
$ j-forward status
$ j phone bluetooth status
$ j phone bluetooth state
```

A test that wants to be sure of the radios before it goes on can wait for them; it does not have to:

```python
members.phone.bluetooth.wait_until_joined()  # the far Bluetooth half is there, and this one is on the air
```

## The bridge: `link: bridge` (BluetoothBridge)

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

**Asked of the device itself.** With a `device` (or, for a `BluetoothBridge` by itself, `identity_from`), the
driver asks the device, and goes on asking:

```yaml
config:
  link: bridge
  device:
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
answered, and all there is without a `device` or `identity_from`. Without an address a half cannot call its device, and
learns the address the first time the device calls.

**Over the radio.** For a physical device beside a real controller there is nothing to ask but the device, on
the air: with `learn_name: true`, a device that has linked is asked its name
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
    type: jumpstarter_driver_bluetooth.driver.Bluetooth
    config:
      transport: tcp-client:127.0.0.1:7300
      link: bridge
      device:
        adb: 127.0.0.1:6520
      name: Cuttlefish phone # until the device has said its own
      class_of_device: 0x5A020C
```

| Parameter | Description | Type | Default |
| --- | --- | --- | --- |
| `name` | The Bluetooth name of the device of this radio | str | `Jumpstarter device` |
| `address` | The device's own Bluetooth address. Empty: learned when the device calls | str | `""` |
| `class_of_device` | The device's class of device (`0x5A020C` a phone, `0x200408` a car's audio) | int | `0x001F00` |
| `identity_from` | Where to ask the device who it is: `{adb: <serial or host:port>}`, with `server_port` (15037), `adb_path` (`adb`), `interval` (10) and `timeout` (15) | dict | the `device` of the entry |
| `learn_name` | Ask a device that has linked for its name over the radio: for a physical device | bool | `false` |
| `call_timeout` | Seconds a call to the device may take | float | `8.0` |
| `pair_timeout` | Seconds the device that started a pairing is kept waiting for the other to agree | float | `25.0` |
| `transport` | The radio's HCI port, as a bumble transport: `tcp-client:127.0.0.1:7300`, `hci-socket:0`, `usb:0`, `serial:…` | str | from `host` and `port` |
| `host`, `port` | Short for `transport: tcp-client:<host>:<port>` | str, int | `127.0.0.1`, `7300` |
| `retry` | Seconds before a radio that is not there, or went away, is looked for again. Give a real controller behind a gateway `5.0` | float | `2.0` |
| `autostart` | Take the HCI port and run the half with the lease's session | bool | `true` |

A virtual device and a physical one have the same driver. What differs is configuration:

| | Virtual radio (rootcanal) | Real controller beside a physical device |
| --- | --- | --- |
| `transport` | `tcp-client:127.0.0.1:7300` (Cuttlefish's `--hci_port` is `7300 + instance_num - 1`) | The HCI port an edge gateway offers (`tcp-client:…`), or the controller itself (`hci-socket:0`, which needs `CAP_NET_ADMIN` and a controller that nothing else uses) |
| Who the device is | `device: {adb: …}`: the device is asked | `name`, `address`, `class_of_device`, and `learn_name: true` |
| `retry` | `2.0` | `5.0`: closing a gateway's port resets the controller |

In a virtual radio the half joins as one more controller. Rootcanal gives every instance the same device
address unless it is told otherwise: two devices that are to pair need two. On a real controller the half is on
real air under the far device's name; a gateway that serves one host at a time is held from `start` to `stop`.

`status()` has, besides `running`, `attached` and `peer`:

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

## The direct path: `link: controller-port` and `link: remote-controller`

Two virtual devices on **one** simulated air, with no bridge between them. rootcanal takes any number of hosts
on its HCI port and gives each a controller and an address of its own. A second Cuttlefish device that is made
without a simulator, and whose HCI goes to the first device's simulator, is simply a second device in the same
room: the two Android stacks pair with each other, once, and each shows the other's real name and the same
six-digit code. Bluetooth LE works as well as BR/EDR, because nothing in the path knows what is said.

```text
 head unit's exporter                                        phone's exporter
 Android - rootcanal (HCI on 7300) - controller-port === remote-controller - tcp_connector - Android
                                     provides bluetooth |  requires bluetooth   (dials 7301)
                                                        |
                                       the forward: one TCP stream, H4
```

Use this where both devices are virtual and one can be made without a simulator. The bridge is for the
rest: a physical device, or two devices that each keep their own simulator.

### The device with the simulator

```yaml
export:
  bluetooth:
    type: jumpstarter_driver_bluetooth.driver.Bluetooth
    config:
      link: controller-port          # BluetoothControllerPort
      transport: tcp-client:127.0.0.1:7300   # --hci_port: 7300 + instance_num - 1
      device:
        adb: 127.0.0.1:6520
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
    type: jumpstarter_driver_bluetooth.driver.Bluetooth
    config:
      link: remote-controller        # BluetoothRemoteController
      device:
        adb: 127.0.0.1:6520          # also: switch the device's Bluetooth off and on for a new controller
      # guest: 127.0.0.1:7301        # where the device's connector dials
      # listen: 127.0.0.1:7311       # where the exporter listens for the forward
      # retry: 1.0
      # autostart: true
```

A `BluetoothRemoteController` by itself takes `adb`, `adb_server_port` (15037), `adb_path` and `adb_timeout`
in place of `device`.

The driver stands between the connector and the forward, and it is there for three reasons:

- **The connector does not survive a cut stream.** It looks for its port once a second until it is there, but
  when its stream goes away its next write kills it, and Cuttlefish stops the whole device. The driver keeps
  the connector's connection for as long as the device runs, whatever the forward does.
- **A cut can fall in the middle of a packet.** The driver passes whole H4 packets only.
- **A path that comes back is a new controller.** It knows nothing of what the stack set up in the old one.
  With a `device` (or `adb`), the driver switches the device's Bluetooth off and on, as a person would. Bonds are the
  stack's own, and stay. A stack that only ever talked to nobody (the far device was not powered on yet) is
  only switched on, in case it gave up.

A connection to the forward counts as a path once the controller at its far end has answered: the driver resets it and asks
it its address first (`controller` in `status`). The exporter takes a connection before it knows whether the
far device is powered on.

`stop` leaves the far controller (the device's Bluetooth hears nothing, and the device stays up); `start`
looks for it again.

### A QEMU guest

A Linux guest in QEMU is a device without a simulator as well. Its HCI is a serial port of the guest (H4, as on
a board whose controller is on a UART) whose other end is a socket beside QEMU: the `serial_ports` of
[`jumpstarter-driver-qemu`](qemu.md). In the guest, `btattach -B /dev/hvc0 -P h4` makes it the kernel's `hci0`,
with BlueZ on top; nothing else in the guest knows that the controller is far away.

```yaml
export:
  qemu:
    type: jumpstarter_driver_qemu.driver.Qemu
    config:
      serial_ports:
        bluetooth: { listen: 127.0.0.1:7301 }   # /dev/hvc0 in the guest
  bluetooth:
    type: jumpstarter_driver_bluetooth.driver.Bluetooth
    config:
      link: remote-controller
      guest: 127.0.0.1:7301
      guest_listens: true              # the serial port listens: the driver dials it
      new_controller: hardware-error   # there is no ADB
```

| Parameter | Description | Default |
| --- | --- | --- |
| `guest_listens` | The device's end listens at `guest` and the driver dials it, and again whenever it went away (QEMU was stopped). `false`: the device's connector dials `guest` | `false` |
| `new_controller` | How the device's stack is told that its controller is a new one. `adb`: an Android device's Bluetooth is switched off and on. `hardware-error`: the device is sent the HCI Hardware Error event, as a controller that lost its state would say; Linux then closes its controller and sets it up again, and the adapter stays powered. `none`: nothing | `adb` with `adb` (or a `device`), else `none` |

Two things the guest's image has to see to. systemd starts a login prompt on a virtio console by itself,
and what it prints there is not HCI: `systemctl mask serial-getty@hvc0`. And `btattach` sets the controller up
once, when it starts: started while no controller is at the far end, the kernel gives the adapter up, and
`btattach` has to be started again (a service that restarts it until `hci0` is up).

A host that is down does not hear a hardware error. A guest whose stack gave up on a controller that never
answered (it was powered on before the far device) has to switch its adapter on itself once the path is there.

`examples/exporter-qemu-headunit.yaml` is such a head unit, with its simulated Wi-Fi beside it, and the lease
template `phone-qemu-headunit` joins it to a Cuttlefish phone that has the simulator.

### Joining them

```yaml
forwards:
  - name: bluetooth
    endpoints:
      - { member: phone, port: bluetooth }     # requires (remote-controller)
      - { member: headunit, port: bluetooth }  # provides (controller-port)
```

```console
$ jmp shell --lease-template phone-headunit-direct
$ j headunit cuttlefish power on     # first: Android on the phone does not finish booting without a controller
$ j phone cuttlefish power on
$ j phone bluetooth status           # peer: true, controller: the phone's address in the head unit's simulator
```

### What it does not do

- It does not work with a physical device, or with a device that keeps its own simulator: use the bridge.
- A QEMU guest has no device view yet: its own stack (BlueZ) is asked by the suite, over SSH or its console.
- One device of the lease must have the simulator and be powered on first. When that device is powered off,
  the other loses its controller, and gets a new one (and its Bluetooth restarted) when it is back.
- The addresses are the simulator's: it gives its hosts consecutive ones, in the order they connect.

## The device's own stack: `device` (AndroidBluetooth)

What a person would read in the device's Bluetooth settings, and do there, over `adb shell`. It is the same for
a Cuttlefish device and a physical Android device, as long as the exporter's ADB reaches it.

```yaml
export:
  bluetooth:
    type: jumpstarter_driver_bluetooth.driver.Bluetooth
    config:
      device:
        adb: 127.0.0.1:6520        # with no `link`: the device's own stack only
```

| Method | What it does on the device |
| --- | --- |
| `get_bluetooth_state()` | `dumpsys bluetooth_manager` for the adapter's state, name, whether it has a link, and its bonded devices; `settings --user 0 get secure bluetooth_address` and the `bluetooth.device.class_of_device` property for the rest; `am get-current-user` |
| `set_enabled(on)` | `cmd bluetooth_manager enable` or `disable`, and `wait-for-state` |
| `factory_reset()` | `cmd bluetooth_manager factoryReset` as root, then `enable` |
| the capture methods | See [Inspection](#inspection) |

What Android gives, and what it does not:

- A bonded device's address is given with its last two bytes only (`XX:XX:XX:XX:17:01`): that is how the
  stack prints it. Its name, class and type (`BR/EDR`, `LE`, `DUAL`) are whole.
- Up to Android 16 the stack says whether the adapter has a link (`adapter.connection_state`), not to whom:
  `connections` is `[]` when it has none and `None` when it has (with a `link`, the link then says whom it
  joined). Android 17 says of every device it knows whether it is linked now: a bond has `connected`, and
  `connections` are the devices that are, bonded or not (`bonded`).
- With a `link`, what the device says takes the place of what the link knows, and so does what it does not
  say: a bridge half that kept the key of a pairing is not a device that has a bond. A device whose Bluetooth
  is off lists no bonds, and `bonds` is `None`.
- `adapter.android_user` is who is in front. On Android Automotive that is not the system user (0) the
  adapter belongs to; the address is read as user 0.
- A device that does not answer (off, booting) has `error` in its state, and nothing else is known of it.
- **There is no forgetting one bond from a shell.** `factory_reset()` resets the device's Bluetooth to what it
  was when new: every bond goes, and so does whatever else the stack kept. It needs root (a `userdebug` or
  `eng` build). `factoryReset` is not among the shell commands of `bluetooth_manager` in the AOSP sources of
  Android 14 to 16 (those are `enable`, `disable`, `enable-ble`, `disable-ble` and `wait-for-state`): a
  device that does not have it says so, the call fails, and the device is left as it was. Android 17 (a
  Cuttlefish `userdebug` build) has it, and there it takes about a second.
- **A factory reset empties the snoop log's mode** too (Android 17): the stack starts again with its build's
  own mode and deletes the log it had. `factory_reset()` sets the mode again if one was set, which starts the
  stack once more, so a recording goes on in a new log. A reset done behind the driver (`adb shell cmd
  bluetooth_manager factoryReset` in a suite) leaves the build's mode: see [Inspection](#inspection).

## Inspection

The inspection methods are on every Bluetooth driver, so that a suite reads the same whatever the device.
In this version one driver records: `AndroidBluetooth`, with **Android's own HCI snoop log**.

```python
phone = members.phone.bluetooth
phone.start_capture()  # may restart the phone's Bluetooth: see below
# ... pair from the head unit ...
found = phone.wait_for_event("connection_complete", timeout=30)
phone.wait_for_event("encryption_change", connection_handle=found["fields"]["connection_handle"])
phone.should_not_have_event("disconnection_complete", within=5)
phone.read_packets("link_key_notification")  # [..., "fields": {"link_key": "<redacted>", ...}]
phone.stop_capture()
phone.read_capture("phone.btsnoop")  # the device's own file: open it in Wireshark
```

```console
$ j phone bluetooth capture start
$ j phone bluetooth capture packets --event connection_complete
$ j phone bluetooth capture save phone.btsnoop
```

How it records:

- The log's mode is the system property `persist.bluetooth.btsnooplogmode`, and has to be `full`. The stack
  reads it only when it starts. **`start_capture` sets it and switches the device's Bluetooth off and on** if
  it was anything else: the device's links are dropped (its bonds stay). A device that is already in `full`
  mode is not restarted. To have no restart in a suite, make the device with the property set. The mode is
  read as root: on Android 17 the shell user is not told it.
- The log is `/data/misc/bluetooth/logs/btsnoop_hci.log` on the device (`snoop_log`), and the log of the run
  before is beside it with `.last`. Both are read as root: a `userdebug` or `eng` build.
- `read_capture` gives the file as the device wrote it: btsnoop version 1 with H4 packets, which Wireshark and
  `btmon -r` read. It is the whole log since the device's Bluetooth last started, **and it has the link keys in
  it**, as any snoop log does. `last=True` gives the log of the run before.
- `read_packets` gives the packets since `start_capture`, decoded with bumble's HCI parser: `index` (the
  packet's place in the log), `time` (the device's clock), `direction`, `type`, `name` and `fields`. Link keys,
  long term keys, PIN codes, passkeys and OOB values are `<redacted>`; what is said above the link (ACL, SCO,
  ISO) is given by its connection handle and length only, since a key may be in it.
- An event is named as a test would say it: `connection_complete`, `Connection Complete` and
  `HCI_CONNECTION_COMPLETE_EVENT` are the same. Keyword arguments narrow it by field, however deep the field
  is (`status="SUCCESS"` finds a command's return parameters). A status or a reason, which the packet has as
  a number, may be said by its name (`status="SUCCESS"`, `reason="REMOTE_USER_TERMINATED_CONNECTION"`) or as
  the number. `since` takes an `index`.
- A device whose mode is not `full` any more (its Bluetooth was reset behind the driver) writes what its
  build writes by itself. On a `userdebug` build that is a *filtered* log, `btsnoop_hci.log.filtered`: every
  command and event, with what the profiles say cut short. It is read in place of the full log, so the
  recording does not end; `capture_status()` has the log that was read in `file`, and the device's `mode`.
- `stop_capture` takes the log and keeps it on the exporter, for `read_capture` and `read_packets` afterwards.
  It leaves the mode `full`. `stop_capture(restore=True)` puts the mode back and restarts Bluetooth once more;
  the log is taken first, because a stack that starts with its log disabled deletes it.
- The stack writes its log in its own time: an event is there a moment after it happened.

Planned, and not in this version: recording at a `BluetoothBridge` (the half's own HCI, for a physical device
whose stack cannot be asked), and device views for iOS and Linux.

## MockBluetooth

A Bluetooth with nothing behind it, for trying a forward without a device. What goes into its port comes back
out, and `exchange` says something into the port and gives back the answer.

```yaml
export:
  bluetooth:
    type: jumpstarter_driver_bluetooth.driver.MockBluetooth
    # config:
    #   direction: requires          # connects (the default), provides or requires
    #   listen: 127.0.0.1:7500       # requires only: where the exporter listens for the forward
```

| `direction` | What it is | With a forward to |
| --- | --- | --- |
| `connects` | One end of a path | Another `connects` mock: `exchange` on either gives back what was said |
| `provides` | A service that echoes | A `requires` mock |
| `requires` | The client of one: `exchange` dials the `listen` address | A `provides` mock |

Its port is called `echo` (`port_name`). A mock is off until it is started:

```console
$ j a bluetooth start
$ j b bluetooth start
$ j a bluetooth exchange hello
hello
```

## Examples

`examples/` has an exporter configuration for a Cuttlefish phone, a Cuttlefish head unit and an edge gateway
beside a tablet, each with its Bluetooth as one entry (and its Wi-Fi, of `jumpstarter-driver-wifi`), and lease
templates that join them: virtual to virtual and physical to virtual. `exporter-cuttlefish-phone-direct.yaml`
and `exporter-cuttlefish-headunit-direct.yaml` are the two devices of the direct path, with the lease template
`phone-headunit-direct`.
