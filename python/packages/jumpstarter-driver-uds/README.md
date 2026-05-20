# UDS Driver

`jumpstarter-driver-uds` provides shared UDS (Unified Diagnostic Services, ISO-14229)
models, client, and abstract interface for Jumpstarter UDS transport drivers.

This package is not used directly -- install a transport-specific driver instead:

- `jumpstarter-driver-uds-doip` -- UDS over DoIP (automotive Ethernet)
- `jumpstarter-driver-uds-can` -- UDS over CAN/ISO-TP

## API Reference

```{eval-rst}
.. autoclass:: jumpstarter_driver_uds.driver.UdsInterface()
```
