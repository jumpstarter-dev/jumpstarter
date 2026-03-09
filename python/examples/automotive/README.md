# Jumpstarter Automotive Diagnostic Example

This example demonstrates how to use Jumpstarter for ECU (Electronic Control
Unit) diagnostics using UDS (Unified Diagnostic Services) over DoIP
(Diagnostics over Internet Protocol).

## What it does

A **stateful mock ECU** simulates a realistic diagnostic target with:

- **Session management** -- default, extended, and programming sessions with
  enforced preconditions (e.g., DID writes require extended session)
- **Security access** -- seed/key challenge-response gating privileged
  operations
- **DID store** -- readable/writable Data Identifiers (VIN, part number,
  software version, supplier ID)
- **DTC memory** -- pre-populated Diagnostic Trouble Codes that can be read
  and cleared, restored on ECU reset
- **Negative responses** -- proper NRC codes when preconditions are violated

The test exercises a complete diagnostic workflow through the full Jumpstarter
pipeline (driver -> gRPC -> client), validating the end-to-end use case.

## Running the tests

From the `python/` directory:

```shell
make pkg-test-jumpstarter_example_automotive
```

## How it maps to real deployments

In production, you would replace the mock ECU with a real ECU connected via
DoIP (TCP/IP) or CAN bus. The exporter configuration would point to the real
ECU's IP address and logical address:

```yaml
apiVersion: jumpstarter.dev/v1alpha1
kind: ExporterConfig
export:
  uds:
    type: jumpstarter_driver_uds_doip.driver.UdsDoip
    config:
      ecu_ip: "192.168.1.100"
      ecu_logical_address: 224  # 0x00E0
      request_timeout: 5
```

The test code using Jumpstarter's client API would remain unchanged -- only the
exporter configuration changes between mock and real hardware.

## Drivers used

- **jumpstarter-driver-uds-doip** -- UDS over DoIP transport
- **jumpstarter-driver-uds** -- UDS service interface (base)
