# DoIP Driver

`jumpstarter-driver-doip` provides raw Diagnostics over Internet Protocol (DoIP, ISO-13400)
operations for Jumpstarter. This driver enables low-level communication with automotive ECUs
over Ethernet, including vehicle discovery, entity status checks, alive checks, and raw
diagnostic message exchange.

For UDS (Unified Diagnostic Services) over DoIP, see `jumpstarter-driver-uds-doip`.

## Installation

```shell
pip3 install --extra-index-url https://pkg.jumpstarter.dev/simple/ jumpstarter-driver-doip
```

## Configuration

| Parameter              | Type   | Default  | Description                                |
|------------------------|--------|----------|--------------------------------------------|
| `ecu_ip`               | str    | required | IP address of the target ECU               |
| `ecu_logical_address`  | int    | required | DoIP logical address of the ECU            |
| `tcp_port`             | int    | 13400    | DoIP TCP port                              |
| `protocol_version`     | int    | 2        | DoIP protocol version (2=2012, 3=2019)     |
| `client_logical_address` | int  | 0x0E00   | Logical address of the client/tester       |
| `auto_reconnect_tcp`   | bool   | false    | Auto-reconnect on TCP connection close     |
| `activation_type`      | int    | 0        | Routing activation type (null to disable)  |

### Example exporter configuration

```{literalinclude} ../../../../../packages/jumpstarter-driver-doip/examples/config.yaml
:language: yaml
```

## API Reference

```{eval-rst}
.. autoclass:: jumpstarter_driver_doip.driver.DoIP()
```
