# Netsim Driver

`jumpstarter-driver-netsim` controls
[Cuttlefish](https://source.android.com/docs/devices/cuttlefish)
netsim virtual radio interfaces through the netsim REST API.
It manages Bluetooth (classic + BLE), WiFi, and UWB radios on
[Cuttlefish](https://source.android.com/docs/devices/cuttlefish) virtual devices:
toggle state and capture HCI packets.

## Installation

```{code-block} console
:substitutions:
$ pip3 install --extra-index-url {{index_url}} jumpstarter-driver-netsim
```

### Prerequisites

- A running netsim instance. Launch Cuttlefish with `--netsim=true` to enable all
  virtual radios (Bluetooth, WiFi, UWB). For Bluetooth only, use `--netsim_bt=true`.
- netsim REST API accessible (see port notes below)

## Configuration

Example exporter configuration:

```yaml
export:
  netsim:
    type: jumpstarter_driver_netsim.driver.Netsim
    config:
      host: localhost
      port: 7681
      netsim_cli: /usr/lib/cuttlefish-common/bin/netsim
```

### Configuration Parameters

| Parameter  | Description                          | Type | Required | Default     |
| ---------- | ------------------------------------ | ---- | -------- | ----------- |
| host       | netsim hostname                      | str  | no       | "localhost" |
| port       | netsim REST API port                 | int  | no       | 7681        |
| netsim_cli | Path to netsim CLI (for captures)    | str  | no       | ""          |

> **Port discovery:** netsimd assigns its REST port as `7681 + netsim_instance_num`.
> The instance number depends on CVD numbering and isn't directly controllable -
> failed CVD creates consume instance numbers. Port 7681 is only correct for
> `netsim_instance_num=0`. Check your CVD config's `netsim_instance_num` field
> and set `port: 7681 + N` in your exporter config.

### ExporterConfig Example

```yaml
apiVersion: jumpstarter.dev/v1alpha1
kind: ExporterConfig
metadata:
  namespace: default
  name: netsim-local
export:
  netsim:
    type: jumpstarter_driver_netsim.driver.Netsim
    config:
      host: localhost
      port: 7681
      netsim_cli: /usr/lib/cuttlefish-common/bin/netsim
```

> **`netsim_cli`**: Required for packet capture toggle (start/stop). The REST
> PATCH endpoint for captures is broken in netsim <=0.3.100. Without this
> config, `start_capture`, `stop_capture`, and `set_capture` will raise an
> error. `list_captures` and `get_capture` (download) work via REST without it.

## Usage

### CLI

```bash
# Health check returns device count
j netsim status

# List all devices and radio states
j netsim devices

# Show a single device (by name or numeric ID)
j netsim device cvd-1
j netsim device 1

# Toggle Bluetooth radios
j netsim radio cvd-1 bt_classic on
j netsim radio cvd-1 ble off

# Toggle WiFi / UWB
j netsim radio cvd-1 wifi on
j netsim radio cvd-1 uwb off

# Raw device patch (full flexibility)
j netsim patch cvd-1 '{"visible": false}'

# Reset all devices (WARNING: host-wide, see below)
j netsim reset

# Packet capture
j netsim capture list
j netsim capture start cvd-1                  # starts BT capture, returns ID
j netsim capture start cvd-1 --chip UWB       # start UWB capture
j netsim capture stop 10                      # stop by capture ID
j netsim capture get 10 -o capture.pcap       # download pcap
```

### Python API

```python
from jumpstarter.common.utils import serve
from jumpstarter_driver_netsim.driver import Netsim

driver = Netsim(
    host="localhost",
    port=7684,  # port = 7681 + netsim_instance_num
    netsim_cli="/usr/lib/cuttlefish-common/bin/netsim",  # for capture toggle
)
with serve(driver) as client:
    # Health check returns device count
    print(client.status())  # e.g. "OK (2 devices)"

    # List devices
    devices = client.list_devices()
    print(devices)

    # Get a single device (name, numeric ID, or substring)
    cvd1 = client.get_device("cvd-1")
    cvd1 = client.get_device("1")  # numeric ID

    # Toggle Bluetooth
    client.set_radio("cvd-1", "bt_classic", "on")
    client.set_radio("cvd-1", "ble", "off")

    # Start packet capture for a device (finds matching capture entry)
    cap_id = client.start_capture("cvd-1")  # returns capture ID
    # ... perform BT operations ...
    client.stop_capture(cap_id)

    # Download pcap
    pcap_data = client.get_capture(cap_id)
    with open("capture.pcap", "wb") as f:
        f.write(pcap_data)

    # Low-level capture control (by capture ID)
    client.set_capture("1", "on")
    client.set_capture("1", "off")

    # Reset all (host-wide!)
    client.reset_devices()
```

## Architecture

```text
┌────────────┐     gRPC      ┌────────────────┐    HTTP     ┌──────────────────┐
│ jmp shell  │──────────────►│ Netsim         │────────────►│ netsim           │
│ (client)   │               │ Driver         │  port 7681  │ (netsimd)        │
└────────────┘               └────────────────┘             └────────┬─────────┘
                                                                     │
                                                              ┌──────┴──────┐
                                                              │  rootcanal  │
                                                              │  (BT HCI)  │
                                                              └──────┬──────┘
                                                                     │
                                                            ┌────────┴────────┐
                                                            │ CVD-1    CVD-2  │
                                                            │ (virtual BT/    │
                                                            │  WiFi/UWB)      │
                                                            └─────────────────┘
```

The driver is a thin REST client that translates Jumpstarter driver calls into
netsim API requests. netsim embeds rootcanal as the virtual Bluetooth HCI
controller, each Cuttlefish CVD auto-registers its radio chips with netsim,
and all CVDs on the same netsim instance share the virtual radio medium.

### Radio Types

| Radio       | CLI name     | Controls                              |
| ----------- | ------------ | ------------------------------------- |
| BT Classic  | `bt_classic` | Classic Bluetooth (A2DP, HFP, etc.)   |
| BLE         | `ble`        | Bluetooth Low Energy                  |
| WiFi        | `wifi`       | Virtual WiFi                          |
| UWB         | `uwb`        | Ultra-Wideband                        |

### Host-Wide Operations

> **Warning:** `reset_devices` (CLI: `j netsim reset`) resets **all** devices
> on the netsim instance, not just the ones you own. On a shared host with
> multiple CVDs or tenants, this will affect everyone. Use with care in
> multi-tenant environments.

## API Reference

### Driver

```{eval-rst}
.. autoclass:: jumpstarter_driver_netsim.driver.Netsim()
   :members:
```

### Client

```{eval-rst}
.. autoclass:: jumpstarter_driver_netsim.client.NetsimClient()
   :members:
```
