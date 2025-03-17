# U-Boot driver

The U-Boot driver is a driver for interacting with the U-Boot bootloader.
This driver does not interact with the DUT directly, instead it should be
configured with backing power and serial drivers.

## Driver configuration

```{literalinclude} uboot.yaml
:language: yaml
```

```{doctest}
:hide:
>>> from jumpstarter.config import ExporterConfigV1Alpha1DriverInstance
>>> ExporterConfigV1Alpha1DriverInstance.from_path("source/api-reference/drivers/uboot.yaml").instantiate()
UbootConsole(...)
```

## Client API

```{eval-rst}
.. autoclass:: jumpstarter_driver_uboot.client.UbootConsoleClient()
    :members:
```
