# UDS over CAN Driver

`jumpstarter-driver-uds-can` provides UDS (Unified Diagnostic Services, ISO-14229)
operations over CAN/ISO-TP (ISO-15765) transport for Jumpstarter. This enables
remote automotive ECU diagnostics over CAN bus.

For UDS over DoIP (automotive Ethernet), see `jumpstarter-driver-uds-doip`.
For raw CAN and ISO-TP operations, see `jumpstarter-driver-can`.

## Installation

```shell
pip3 install --extra-index-url https://pkg.jumpstarter.dev/simple/ jumpstarter-driver-uds-can
```

## Configuration

| Parameter          | Type   | Default      | Description                                     |
|--------------------|--------|--------------|-------------------------------------------------|
| `channel`          | str    | required     | CAN channel (e.g. `can0`, `vcan0`)              |
| `interface`        | str    | `socketcan`  | python-can interface type                        |
| `rxid`             | int    | required     | ISO-TP receive arbitration ID                    |
| `txid`             | int    | required     | ISO-TP transmit arbitration ID                   |
| `request_timeout`  | float  | 5.0          | UDS request timeout in seconds                   |
| `isotp_params`     | IsoTpParams | `{}`    | ISO-TP parameters (stmin, blocksize, etc.)       |

### ISO-TP Parameters

| Parameter                     | Type   | Default | Description                          |
|-------------------------------|--------|---------|--------------------------------------|
| `stmin`                       | int    | 0       | Minimum separation time (ms)         |
| `blocksize`                   | int    | 8       | Number of consecutive frames         |
| `tx_data_length`              | int    | 8       | CAN frame data length                |
| `max_frame_size`              | int    | 4095    | Maximum ISO-TP frame size            |
| `can_fd`                      | bool   | false   | Use CAN FD                           |
| `blocking_send`               | bool   | false   | Use blocking send                    |

### Example exporter configuration

```yaml
export:
  uds:
    type: jumpstarter_driver_uds_can.driver.UdsCan
    config:
      channel: "can0"
      interface: "socketcan"
      rxid: 1601  # 0x641
      txid: 1602  # 0x642
      request_timeout: 5
      isotp_params:
        stmin: 32
        blocksize: 8
        tx_data_length: 8
```

## Client API

The client API is shared by all UDS transport drivers via `jumpstarter-driver-uds`.
See the UDS driver documentation for the full API reference.

| Method                                | Description                                  |
|---------------------------------------|----------------------------------------------|
| `change_session(session)`             | Change diagnostic session                    |
| `ecu_reset(reset_type)`              | Reset ECU                                    |
| `tester_present()`                    | Keep session alive                           |
| `read_data_by_identifier(did_list)`   | Read DID values                              |
| `write_data_by_identifier(did, value)`| Write DID value                              |
| `request_seed(level)`                 | Request security access seed                 |
| `send_key(level, key)`               | Send security access key                     |
| `clear_dtc(group)`                    | Clear diagnostic trouble codes               |
| `read_dtc_by_status_mask(mask)`       | Read DTCs matching status mask               |
