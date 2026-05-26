# UDS over DoIP Driver

`jumpstarter-driver-uds-doip` provides UDS (Unified Diagnostic Services, ISO-14229)
operations over DoIP (Diagnostics over Internet Protocol, ISO-13400) transport for
Jumpstarter. This enables remote automotive ECU diagnostics over Ethernet.

For raw DoIP operations (vehicle discovery, entity status), see `jumpstarter-driver-doip`.
For UDS over CAN/ISO-TP, see `jumpstarter-driver-uds-can`.

## Installation

```shell
pip3 install --extra-index-url https://pkg.jumpstarter.dev/simple/ jumpstarter-driver-uds-doip
```

## Configuration

| Parameter              | Type   | Default  | Description                                |
|------------------------|--------|----------|--------------------------------------------|
| `ecu_ip`               | str    | required | IP address of the target ECU               |
| `ecu_logical_address`  | int    | required | DoIP logical address of the ECU            |
| `tcp_port`             | int    | 13400    | DoIP TCP port                              |
| `protocol_version`     | int    | 2        | DoIP protocol version                      |
| `client_logical_address` | int  | 0x0E00   | Logical address of the client/tester       |
| `auto_reconnect_tcp`   | bool   | false    | Auto-reconnect on TCP connection close     |
| `request_timeout`      | float  | 5.0      | UDS request timeout in seconds             |

### Example exporter configuration

```{literalinclude} ../../../../../packages/jumpstarter-driver-uds-doip/examples/config.yaml
:language: yaml
```

## API Reference

```{eval-rst}
.. autoclass:: jumpstarter_driver_uds_doip.driver.UdsDoip()
```
