# U-Boot driver

`jumpstarter-driver-uboot` provides functionality for interacting with the
U-Boot bootloader. This driver does not interact with the DUT directly, instead
it should be configured with backing power and serial drivers.

## Installation

```shell
pip install jumpstarter-driver-uboot
```

## Configuration

Example configuration:

```{literalinclude} uboot.yaml
:language: yaml
```

```{doctest}
:hide:
>>> from jumpstarter.config.exporter import ExporterConfigV1Alpha1DriverInstance
>>> ExporterConfigV1Alpha1DriverInstance.from_path("source/reference/package-apis/drivers/uboot.yaml").instantiate()
UbootConsole(...)
```

## API Reference

```{eval-rst}
.. autoclass:: jumpstarter_driver_uboot.client.UbootConsoleClient()
    :members:
```
