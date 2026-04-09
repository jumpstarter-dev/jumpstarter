# UDS Driver (Shared Interface)

`jumpstarter-driver-uds` provides shared UDS (Unified Diagnostic Services, ISO-14229)
models, client, and abstract interface for Jumpstarter UDS transport drivers.

This package is not used directly -- install a transport-specific driver instead:

- `jumpstarter-driver-uds-doip` -- UDS over DoIP (automotive Ethernet)
- `jumpstarter-driver-uds-can` -- UDS over CAN/ISO-TP

## Client API

All UDS transport drivers share the same client interface:

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
