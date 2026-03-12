# XCP Driver

`jumpstarter-driver-xcp` provides XCP (Universal Measurement and Calibration Protocol) support
for Jumpstarter, enabling remote measurement, calibration, DAQ (data acquisition), and
programming of XCP-enabled ECUs.

It wraps the [pyXCP](https://github.com/christoph2/pyxcp) library and supports Ethernet (TCP/UDP),
CAN, USB, and Serial (SxI) transports.

## Installation

```shell
pip3 install --extra-index-url https://pkg.jumpstarter.dev/simple/ jumpstarter-driver-xcp
```

## Configuration

### Ethernet (TCP)

```yaml
export:
  xcp:
    type: jumpstarter_driver_xcp.driver.Xcp
    config:
      transport: ETH
      host: "192.168.1.100"
      port: 5555
      protocol: TCP
```

### Ethernet (UDP)

```yaml
export:
  xcp:
    type: jumpstarter_driver_xcp.driver.Xcp
    config:
      transport: ETH
      host: "192.168.1.100"
      port: 5555
      protocol: UDP
```

### CAN

```yaml
export:
  xcp:
    type: jumpstarter_driver_xcp.driver.Xcp
    config:
      transport: CAN
      can_interface: vector
      channel: 0
      bitrate: 500000
      can_id_master: 0x7E0
      can_id_slave: 0x7E1
```

### Using a pyXCP Config File

For advanced configuration (seed & key, DAQ policies, etc.), provide a
[pyXCP configuration file](https://pyxcp.readthedocs.io/en/latest/configuration.html):

```yaml
export:
  xcp:
    type: jumpstarter_driver_xcp.driver.Xcp
    config:
      transport: ETH
      config_file: /path/to/xcp_config.py
```

## Configuration Parameters

| Parameter | Type | Default | Description |
|---|---|---|---|
| `transport` | `str` | `ETH` | Transport layer: `ETH`, `CAN`, `USB`, `SXI` |
| `host` | `str` | `localhost` | IP address or hostname (Ethernet only) |
| `port` | `int` | `5555` | Port number (Ethernet only) |
| `protocol` | `str` | `TCP` | `TCP` or `UDP` (Ethernet only) |
| `can_interface` | `str` | `None` | python-can interface name (CAN only) |
| `channel` | `str\|int` | `None` | CAN channel (CAN only) |
| `bitrate` | `int` | `None` | CAN bitrate in bits/s (CAN only) |
| `can_id_master` | `int` | `None` | CAN ID for master -> slave (CAN only) |
| `can_id_slave` | `int` | `None` | CAN ID for slave -> master (CAN only) |
| `config_file` | `str` | `None` | Path to a pyXCP config file (overrides individual params) |

## API Reference

### Session Management

- `connect(mode=0)` - Connect to the XCP slave, returns negotiated properties
- `disconnect()` - Disconnect from the XCP slave
- `get_id(id_type=1)` - Get the slave identifier
- `get_status()` - Get session status and resource protection

### Security

- `unlock(resources=None)` - Perform seed & key unlock for protected resources

### Memory Access (Measurement / Calibration)

- `upload(length, address, ext=0)` - Read memory from the slave
- `download(address, data, ext=0)` - Write data to the slave memory
- `set_mta(address, ext=0)` - Set the Memory Transfer Address
- `build_checksum(block_size)` - Compute checksum over a memory block

### DAQ (Data Acquisition)

- `get_daq_info()` - Get DAQ processor, resolution, and event channel info
- `free_daq()` - Free all DAQ lists
- `alloc_daq(daq_count)` - Allocate DAQ lists
- `alloc_odt(daq_list_number, odt_count)` - Allocate ODTs
- `alloc_odt_entry(daq_list_number, odt_number, odt_entries_count)` - Allocate ODT entries
- `set_daq_ptr(daq_list, odt, entry)` - Set DAQ list pointer
- `write_daq(bit_offset, size, ext, address)` - Configure what to measure
- `set_daq_list_mode(mode, daq_list, event, prescaler, priority)` - Set DAQ list mode
- `start_stop_daq_list(mode, daq_list)` - Start/stop a single DAQ list
- `start_stop_synch(mode)` - Start/stop all DAQ lists synchronously

### Programming (Flashing)

- `program_start()` - Begin programming sequence
- `program_clear(clear_range, mode=0)` - Erase memory range
- `program(data, block_length=0)` - Download program data
- `program_reset()` - Reset slave after programming

## Example Usage

```python
from jumpstarter.common.utils import env

with env() as client:
    xcp = client.xcp

    info = xcp.connect()
    print(f"Max CTO: {info.max_cto}, Max DTO: {info.max_dto}")

    xcp.unlock()

    data = xcp.upload(4, 0x1000)
    print(f"Memory at 0x1000: {data.hex()}")

    xcp.download(0x2000, b"\x42\x00\x00\x00")

    xcp.disconnect()
```
