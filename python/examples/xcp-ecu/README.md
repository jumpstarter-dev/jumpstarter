# XCP ECU Example

Representative end-to-end tests demonstrating XCP (Universal Measurement and
Calibration Protocol) workflows with Jumpstarter.

## What This Example Tests

A stateful mock ECU (`mock_ecu.py`) simulates a realistic automotive ECU with:

- **Addressable memory regions**: calibration parameters, measurement signals, and a 64 KB flash area
- **Resource protection**: CAL/PAG and PGM resources require unlock before modification
- **DAQ list management**: dynamic allocation, ODT configuration, start/stop
- **Flash programming lifecycle**: strict sequence enforcement (start → clear → program → reset)

### Test Scenarios

| Test | Workflow |
|------|----------|
| `test_full_measurement_and_calibration_workflow` | Connect → identify → read measurements → unlock → calibrate → verify → checksum → disconnect |
| `test_full_flash_programming_workflow` | Connect → unlock → erase flash → write firmware → verify → reset |
| `test_full_daq_configuration_workflow` | Connect → query DAQ → allocate lists → configure ODTs → start/stop → cleanup |
| `test_read_all_calibration_parameters` | Verify all pre-populated calibration values |
| `test_read_all_measurement_signals` | Verify all pre-populated measurement signals |
| `test_multiple_calibration_changes` | Modify several parameters and verify independently |
| `test_program_clear_before_start_fails` | Sequence enforcement: clear without start |
| `test_program_without_clear_fails` | Sequence enforcement: program without clear |
| `test_operations_before_connect_fail` | Connection-required enforcement |
| `test_reconnect_preserves_memory` | Non-volatile memory across disconnect/reconnect |

## Running

```bash
cd python
uv run --directory examples/xcp-ecu pytest -v
```
