# XCP Driver

`jumpstarter-driver-xcp` provides XCP (Universal Measurement and Calibration Protocol) support
for Jumpstarter, enabling remote measurement, calibration, DAQ (data acquisition), and
programming of XCP-enabled ECUs.

It wraps the [pyXCP](https://github.com/christoph2/pyxcp) library and supports Ethernet (TCP/UDP),
CAN, USB, and Serial (SxI) transports.

## Installation

```{code-block} shell
pip3 install --extra-index-url https://pkg.jumpstarter.dev/simple/ jumpstarter-driver-xcp
```

## Configuration

### Ethernet (TCP)

```{literalinclude} ../../../../../packages/jumpstarter-driver-xcp/examples/config.yaml
:language: yaml
```

### Ethernet (UDP)

```{literalinclude} ../../../../../packages/jumpstarter-driver-xcp/examples/config_udp.yaml
:language: yaml
```

### CAN

```{literalinclude} ../../../../../packages/jumpstarter-driver-xcp/examples/config_can.yaml
:language: yaml
```

### Using a pyXCP Config File

For advanced configuration (seed & key, DAQ policies, etc.), provide a
[pyXCP configuration file](https://pyxcp.readthedocs.io/en/latest/configuration.html):

```{literalinclude} ../../../../../packages/jumpstarter-driver-xcp/examples/config_pyxcp.yaml
:language: yaml
```

### Configuration Parameters

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

## Usage

```{literalinclude} ../../../../../packages/jumpstarter-driver-xcp/examples/usage.py
:language: python
```

## API Reference

```{eval-rst}
.. autoclass:: jumpstarter_driver_xcp.driver.Xcp()
```
