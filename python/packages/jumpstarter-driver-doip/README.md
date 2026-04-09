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

```yaml
export:
  doip:
    type: jumpstarter_driver_doip.driver.DoIP
    config:
      ecu_ip: "192.168.1.100"
      ecu_logical_address: 224  # 0x00E0
```

## Client API

| Method                         | Description                                      |
|--------------------------------|--------------------------------------------------|
| `entity_status()`              | Request DoIP entity status                       |
| `alive_check()`                | Request alive check                              |
| `diagnostic_power_mode()`      | Request diagnostic power mode                    |
| `request_vehicle_identification()` | Request vehicle identification (VIN, EID, etc.) |
| `routing_activation(type)`     | Request routing activation                       |
| `send_diagnostic(payload)`     | Send raw diagnostic payload bytes                |
| `receive_diagnostic(timeout)`  | Receive raw diagnostic response bytes            |
| `reconnect(close_delay)`       | Reconnect after ECU reset                        |
| `close_connection()`           | Close the DoIP connection                        |
