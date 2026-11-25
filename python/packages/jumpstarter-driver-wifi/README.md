# Wi-Fi driver

`jumpstarter-driver-wifi` is for a device's Wi-Fi, on a simulated medium or a real test network. A Wi-Fi
driver gives the device's Wi-Fi as a **port** (JEP-0016, multi-exporter port forwarding). A forward of a lease
joins the ports of two devices, wherever their exporters run. The test is not in the path.

Every driver has the same interface and the same client, shaped like those of
[`jumpstarter-driver-bluetooth`](bluetooth.md): a suite says `phone.wifi`, whatever the device.

| Mode | Driver | Port | What crosses it | |
| --- | --- | --- | --- | --- |
| A virtual medium | `WifiMedium` | `hwsim`, `connects`, `ieee80211-frames` | 802.11 frames, between the simulated media ([wmediumd](https://github.com/bcopeland/wmediumd)) of two virtual devices (Cuttlefish, other `mac80211_hwsim` setups) | Working |
| One port over a real network | `WifiGateway` | `wifi-service`, `requires` | One TCP connection for each connection a physical device makes to one port of a real test network beside it (an edge gateway) | Working |
| A whole network over a real network | | | A physical device is on one IP network with a member of the lease, not at one port of it | Planned |
| The radio handed to the far device | | | A real Wi-Fi adapter beside a physical device is the far (virtual) device's own radio | Experimental: it needs an adapter that can be handed over |

## Installation

```{code-block} console
:substitutions:
$ pip3 install --extra-index-url {{index_url}} jumpstarter-driver-wifi
```

## The interface

```{eval-rst}
.. autoclass:: jumpstarter_driver_wifi.interface.WifiInterface()
    :members:
```

```{eval-rst}
.. autoclass:: jumpstarter_driver_wifi.client.WifiClient()
    :members:
```

`status()` has at least `running`, `attached` (the local end has the radio) and `peer` (a far end is joined).
`get_wifi_state()` says what a driver can do, in `capabilities`, and what is known of the device's Wi-Fi. Both
drivers of this version are links, and neither asks the device's own Wi-Fi: `adapter` and `connections` are
`None`.

A driver starts by itself with the exporter's session for a lease and stops at the end of it (`autostart`,
true by default). A lease whose forwards join two devices therefore has the bridges set up before a test does
anything: a forward that is `Listening` or `Connected` has a running driver at each end. `start` and `stop`
remain, to take a radio off the air and put it back; with `autostart: false` nothing runs until `start`.

A driver declares its port by itself: an exporter configuration needs no `ports` key for it. Give one to
rename the port (two radios of one kind on one exporter need two names) or to change a local address:

```yaml
export:
  wifi:
    type: jumpstarter_driver_wifi.driver.WifiMedium
    ports:
      - name: hwsim-front
        direction: connects
        protocol: ieee80211-frames
```

Two things are every driver's own business, because no network between exporters can do them:

- whatever has a deadline: a simulator's acknowledgement, a radio's timing;
- telling the far side's traffic from this side's, so that nothing goes round in a circle.

That is why the port that puts two media on one air is a `connects` port: neither side is the service. Each
driver runs its half of a bridge beside its own medium, and the forward between the two halves is a path
(JEP-0016, DD-11). A port's `protocol` says what crosses it; the controller refuses a forward between two
ports that say different things.

### Inspection

The inspection methods of the Bluetooth drivers are on every Wi-Fi driver under the same names
(`start_capture`, `stop_capture`, `capture_status`, `read_capture`, `read_packets`, `wait_for_event`,
`should_not_have_event`), so that a suite written against them stays as it is. No Wi-Fi driver records in
this version: each raises `NotImplementedError` and says what is planned to provide it:

- at the medium: a pcapng of the 802.11 frames that cross a `WifiMedium`;
- at the device: `tcpdump` on the device's own interface.

## Joining two devices: the forwards of a lease

The Wi-Fi of two devices is joined by the lease, not by the test. A lease template names the members and the
forwards (`examples/lease-templates.yaml`):

```yaml
apiVersion: jumpstarter.dev/v1alpha1
kind: LeaseTemplate
metadata:
  name: phone-headunit-wifi
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
```

or, for a lease made by hand:

```console
$ jmp create lease --member phone=type=phone --member headunit=type=head-unit \
    --forward wifi=phone.hwsim,headunit.hwsim
```

```console
$ jmp shell --lease-template phone-headunit-wifi
$ j-forward status
$ j phone wifi status
```

A test that wants to be sure of the path before it goes on can wait for it; it does not have to:

```python
members.phone.wifi.wait_until_joined()  # the path between the two Wi-Fi bridges is open
```

## A virtual medium: WifiMedium

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
    type: jumpstarter_driver_wifi.driver.WifiMedium
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

## One port over a real network: WifiGateway

A physical device has a real Wi-Fi radio. An edge gateway beside it (a Raspberry Pi, say) runs an access point
for a test network and offers **one TCP port** on it. A device on the Wi-Fi that connects to that port is
talking to whatever is attached behind the gateway. With this driver, that is a service of another member of
the lease: a web server inside a virtual head unit, for example.

```text
 tablet ))) test Wi-Fi ── gateway :8080 ─ attach :7410 ─ WifiGateway ─ wifi-service ═══ forward ═══ web ── service
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
    type: jumpstarter_driver_wifi.driver.WifiGateway
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

## Examples

`examples/` has an exporter configuration for the Wi-Fi of a Cuttlefish phone, a Cuttlefish head unit and an
edge gateway beside a tablet, and lease templates that join them: virtual to virtual, and physical to virtual.
The examples of `jumpstarter-driver-bluetooth` have the same devices with their Bluetooth beside it.
