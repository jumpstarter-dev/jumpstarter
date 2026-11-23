# Radio driver

`jumpstarter-driver-radio` is for a device's radios: Wi-Fi, Bluetooth, UWB, simulated or real. A radio driver
gives one radio as **ports**. Join the ports of two devices and they are on the same air, wherever their
exporters run.

It is to radios what `jumpstarter-driver-power` is to power: one interface, and a driver for each kind of
radio behind it. The ports are JEP-0015's: a radio driver sets up its own end and says what it offers; a lease's
forwards join the ports, and the test client is never in the path.

| Driver | Radio | Port | What crosses it |
| --- | --- | --- | --- |
| `WmediumdWifi` | The Wi-Fi of a device simulated with [wmediumd](https://github.com/bcopeland/wmediumd) (Cuttlefish, other `mac80211_hwsim` setups) | `hwsim`, `connects` | 802.11 frames |
| `MockRadio` | None: what goes into its port comes back out | `echo`, `connects` | Anything |

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
`ports()` gives each port's name, direction (`provides`, `requires` or `connects`), protocol, transport and
attachment, as JEP-0015 names them, and the child driver through which the port is reached over a lease until
forwards exist.

Two things are every radio driver's own business, because no network between exporters can do them:

- whatever has a deadline: a simulator's acknowledgement, a real radio's timing;
- telling the far side's traffic from this side's, so that nothing goes round in a circle.

A port's `protocol` says what crosses it. Only ports that speak the same may be joined, and the level decides
what a test of the join means: joined at 802.11 frames, the devices' own Wi-Fi stacks meet; joined at a
connection, only that connection's traffic does.

## WmediumdWifi

### How it works

```text
 exporter A                                              exporter B
 ┌──────────────────────────────┐                        ┌──────────────────────────────┐
 │ guest ── wmediumd ── bridge ─┼── frames port ══════ frames port ──┼─ bridge ── wmediumd ── guest │
 └──────────────────────────────┘     (a byte stream)    └──────────────────────────────┘
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

### The frame port

A stream of records. Each is a 4-byte big-endian length followed by that many bytes: the framing of QEMU's
`stream` and `socket` network devices, so the same kind of port, joined the same way, carries Ethernet between
QEMU guests without a bridge of any kind. A Wi-Fi record's bytes are:

| Bytes | Meaning |
| --- | --- |
| 4, big-endian | The frequency the frame was sent on, in MHz |
| the rest | One 802.11 frame, without FCS |

One connection at a time: a new one replaces the one before it. A record that cannot be a frame closes the
connection. A peer that does not read loses frames, as a radio out of range would; it never holds the medium up.

`ports()` reports it as `hwsim`, `connects` (neither side leads: two of them form one path), protocol
`ieee80211-frames`.

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
| `host`, `port` | Where the frame port listens | str, int | `127.0.0.1`, `7310` |
| `station` | A station of the medium's configuration that no device uses; the bridge sends as it | str | `02:15:b3:00:00:00` |
| `ignore` | Senders that are not passed on, as address prefixes | list | `["02:00:00:00", "02:15:b2"]` |
| `launcher_socket` | Run the bridge through `jumpstarter-exec` over this launcher socket. Empty runs it in the exporter's own container | str | `$JUMPSTARTER_LAUNCHER_SOCKET` |
| `python` | The Python of the container the bridge runs in | str | `python3` |

The defaults are Cuttlefish's. Its devices all have the same built-in access point (`02:00:00:00:…`) and the
same factory addresses (`02:15:b2:…`), which must not meet on one air; the randomized addresses Android uses
for scanning, for connecting and for its own access point do not clash, and are what crosses the bridge.

## Usage

```python
wifi = client.wifi
wifi.wait_until_attached()    # start the bridge; returns once it has joined the Wi-Fi medium
wifi.status()                 # {"running": True, "attached": True, "peer": False, "heard": 0, "sent": 0, ...}
wifi.ports()                  # [RadioPort(name="hwsim", direction="connects", protocol="ieee80211-frames", ...)]
with wifi.frames.stream() as frames:   # the frame port, through the lease
    ...
wifi.stop()
```

```console
$ j wifi start
$ j wifi status
$ j wifi ports
$ j wifi stop
```

### What it does not do

- It does not join anything. Until JEP-0015's forwards exist, something has to carry the bytes between two
  frame ports; any TCP relay will.
- It does not model distance or loss between the two sides: each medium acknowledges a frame for the far side
  as it would for a station in range.
- It keeps the frame port open across a power cycle of the device but closes the connection, since the far
  side has to learn of the new medium.
