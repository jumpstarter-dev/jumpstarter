# gPTP driver

`jumpstarter-driver-gptp` provides IEEE 802.1AS (gPTP) and IEEE 1588 (PTPv2)
time synchronization for Jumpstarter. It manages the
[linuxptp](https://linuxptp.nwtime.org/) stack (`ptp4l` and `phc2sys`) as
supervised subprocesses, enabling precise clock synchronization between an
exporter host and a target device over automotive Ethernet or standard IP networks.

gPTP is the foundation of Time-Sensitive Networking (TSN), required for
applications like sensor fusion, ADAS, and synchronized diagnostics in
automotive ECU testing.

## Installation

```{code-block} console
:substitutions:
$ pip3 install --extra-index-url {{index_url}} jumpstarter-driver-gptp
```

**System requirements** (on the exporter host):

```console
# Debian/Ubuntu
$ sudo apt install linuxptp ethtool

# Fedora/RHEL
$ sudo dnf install linuxptp ethtool
```

## Configuration

### gPTP Slave (802.1AS, automotive Ethernet)

The most common automotive scenario — synchronize to an external grandmaster:

```yaml
export:
  gptp:
    type: jumpstarter_driver_gptp.driver.Gptp
    config:
      interface: eth0
      domain: 0
      profile: gptp
      transport: L2
      role: slave
      sync_system_clock: true
```

### IEEE 1588 UDP mode

For networks that use standard PTPv2 over UDP:

```yaml
export:
  ptp:
    type: jumpstarter_driver_gptp.driver.Gptp
    config:
      interface: eth0
      domain: 0
      profile: default
      transport: UDPv4
      role: auto
```

### gPTP Grandmaster

Force this host to act as the PTP grandmaster:

```yaml
export:
  gptp:
    type: jumpstarter_driver_gptp.driver.Gptp
    config:
      interface: eth0
      profile: gptp
      role: master
      sync_system_clock: false
```

### Combined with SOME/IP

gPTP provides the time base for other automotive Ethernet protocols:

```yaml
export:
  gptp:
    type: jumpstarter_driver_gptp.driver.Gptp
    config:
      interface: eth0
      profile: gptp
      role: auto
  someip:
    type: jumpstarter_driver_someip.driver.SomeIp
    config:
      host: 192.168.1.100
```

### Config parameters

| Parameter          | Description                                          | Type       | Required | Default  |
| ------------------ | ---------------------------------------------------- | ---------- | -------- | -------- |
| interface          | Network interface for PTP (e.g. `eth0`, `enp3s0`)    | str        | yes      |          |
| domain             | PTP domain number (0-127)                            | int        | no       | 0        |
| profile            | `"gptp"` (IEEE 802.1AS) or `"default"` (IEEE 1588)  | str        | no       | `"gptp"` |
| transport          | `"L2"`, `"UDPv4"`, or `"UDPv6"`                     | str        | no       | `"L2"`   |
| role               | `"master"`, `"slave"`, or `"auto"` (BMCA election)  | str        | no       | `"auto"` |
| sync_system_clock  | Run `phc2sys` to sync CLOCK_REALTIME to PHC          | bool       | no       | true     |
| ptp4l_extra_args   | Additional ptp4l command-line arguments              | list[str]  | no       | []       |

## PTP Standards Reference

### IEEE 802.1AS (gPTP) vs IEEE 1588 (PTPv2)

| Feature           | 802.1AS (gPTP)               | IEEE 1588 (PTPv2)             |
| ----------------- | ---------------------------- | ----------------------------- |
| Transport         | Layer 2 only                 | L2, UDPv4, UDPv6             |
| Timestamping      | Hardware required             | HW or software               |
| Accuracy          | Sub-microsecond              | Sub-microsecond to ms         |
| Use case          | Automotive, industrial TSN   | General purpose               |
| Profile setting   | `profile: gptp`              | `profile: default`            |

### Port State Machine

PTP ports transition through these states:

```
INITIALIZING → LISTENING → SLAVE (synchronized to master)
                         → MASTER (elected as grandmaster)
                         → PASSIVE (backup, not active)
                         → FAULTY (error detected)
```

### Servo States

The clock servo tracks synchronization quality:

- **s0** (unlocked): Initial state, no sync
- **s1** (calibrating): Frequency adjustment in progress
- **s2** (locked): Fully synchronized, offset stable

## API Reference

### GptpClient

```{eval-rst}
.. autoclass:: jumpstarter_driver_gptp.client.GptpClient()
    :members: start, stop, status, get_offset, get_port_stats,
              get_clock_identity, get_parent_info, set_priority1,
              is_synchronized, wait_for_sync, monitor
```

## Examples

### Basic lifecycle

```python
with serve(Gptp(interface="eth0")) as gptp:
    gptp.start()

    # Wait for synchronization (up to 30 seconds)
    if gptp.wait_for_sync(timeout=30.0):
        offset = gptp.get_offset()
        print(f"Synchronized! Offset: {offset.offset_from_master_ns:.0f} ns")
    else:
        print("Sync timeout")

    gptp.stop()
```

### Monitoring sync events

```python
with serve(Gptp(interface="eth0")) as gptp:
    gptp.start()
    for event in gptp.monitor():
        print(f"[{event.event_type}] offset={event.offset_ns:.0f}ns state={event.port_state}")
        if event.event_type == "fault":
            break
    gptp.stop()
```

### Force master role

```python
with serve(Gptp(interface="eth0", role="auto")) as gptp:
    gptp.start()
    gptp.wait_for_sync()

    # Override BMCA: become grandmaster
    gptp.set_priority1(0)
    status = gptp.status()
    assert status.port_state.value == "MASTER"
```

### Using MockGptp in tests

```python
from jumpstarter_driver_gptp.driver import MockGptp
from jumpstarter.common.utils import serve

def test_my_application():
    with serve(MockGptp()) as gptp:
        gptp.start()
        assert gptp.is_synchronized()
        assert abs(gptp.get_offset().offset_from_master_ns) < 1000
        gptp.stop()
```

## CLI Commands

When used inside `jmp shell`, the driver provides these commands:

```console
$ j gptp start              # Start PTP synchronization
$ j gptp stop               # Stop PTP synchronization
$ j gptp status             # Show sync status
$ j gptp offset             # Show current clock offset
$ j gptp monitor -n 20      # Monitor 20 sync events
$ j gptp set-priority 0     # Force grandmaster role
```

## Hardware Requirements

### PTP-capable NICs

For sub-microsecond accuracy, the network interface must support hardware
timestamping. Common PTP-capable NICs:

- Intel i210, i225, i226 (automotive-grade variants available)
- Intel X710, XL710, E810
- Broadcom BCM5719, BCM5720
- TI AM65x / Jacinto 7 (embedded automotive)
- NXP S32G (automotive gateway)

### Verifying hardware timestamping

```console
$ ethtool -T eth0
# Look for:
#   hardware-transmit
#   hardware-receive
#   hardware-raw-clock
```

If hardware timestamping is not available, the driver automatically falls back
to software timestamping (`-S` flag) with a warning. Software timestamping
provides millisecond-level accuracy, sufficient for development but not for
production TSN.

## Troubleshooting

### Permission denied

`ptp4l` requires `CAP_NET_RAW` (or root) for Layer 2 transport and hardware
timestamping:

```console
$ sudo setcap cap_net_raw+ep $(which ptp4l)
# or run the exporter as root
```

### No hardware timestamping

If you see "falling back to software timestamping":
1. Check NIC support: `ethtool -T <interface>`
2. Verify the NIC driver is loaded: `lsmod | grep <driver>`
3. Some virtualized NICs (virtio, veth) only support software timestamping

### ptp4l not found

Ensure linuxptp is installed:
```console
$ which ptp4l
/usr/sbin/ptp4l
```

### No sync achieved

- Verify the link partner (DUT) is running a gPTP stack
- Check physical layer: `ethtool <interface>` should show link up
- Review ptp4l logs in the exporter output
- Ensure both ends use the same domain number and transport
