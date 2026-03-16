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

```yaml
export:
  uds:
    type: jumpstarter_driver_uds_doip.driver.UdsDoip
    config:
      ecu_ip: "192.168.1.100"
      ecu_logical_address: 224  # 0x00E0
      request_timeout: 5
```

## Client API

| Method                                | Description                                  |
|---------------------------------------|----------------------------------------------|
| `change_session(session)`             | Change diagnostic session (default/extended/programming/safety) |
| `ecu_reset(reset_type)`              | Reset ECU (hard/soft/key_off_on)             |
| `tester_present()`                    | Keep session alive                           |
| `read_data_by_identifier(did_list)`   | Read DID values                              |
| `write_data_by_identifier(did, value)`| Write DID value                              |
| `request_seed(level)`                 | Request security access seed                 |
| `send_key(level, key)`               | Send security access key                     |
| `clear_dtc(group)`                    | Clear diagnostic trouble codes               |
| `read_dtc_by_status_mask(mask)`       | Read DTCs matching status mask               |

### Session Types

- `default` -- Default diagnostic session
- `programming` -- Programming session
- `extended` -- Extended diagnostic session
- `safety` -- Safety system diagnostic session

### Reset Types

- `hard` -- Hard reset
- `key_off_on` -- Key off/on reset
- `soft` -- Soft reset
