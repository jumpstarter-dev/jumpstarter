# UDS Driver

`jumpstarter-driver-uds` provides shared UDS (Unified Diagnostic Services, ISO-14229)
models, client, and abstract interface for Jumpstarter UDS transport drivers.

This package is not used directly - install a transport-specific driver instead:

- `jumpstarter-driver-uds-doip` - UDS over DoIP (automotive Ethernet)
- `jumpstarter-driver-uds-can` - UDS over CAN/ISO-TP

## Installation

```{code-block} console
:substitutions:
$ pip3 install --extra-index-url {{index_url}} jumpstarter-driver-uds
```

## Configuration

`jumpstarter-driver-uds` provides the shared UDS interface and client. It does
not have its own exporter configuration because it is not used directly as a
driver. Configuration is done on the transport-specific drivers:

- `jumpstarter-driver-uds-can` - UDS over CAN/ISO-TP
- `jumpstarter-driver-uds-doip` - UDS over DoIP (automotive Ethernet)

Refer to those driver READMEs for exporter configuration examples.

## API Reference

```{eval-rst}
.. autoclass:: jumpstarter_driver_uds.driver.UdsInterface()
```
