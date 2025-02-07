# SDWire driver

The SDWire driver is an storgate multiplexer driver for using the SDWire
multiplexer. This device multiplexes an SD card between the DUT and the
exporter host.

## Driver Configuration

```{literalinclude} sdwire.yaml
:language: yaml
```

```{doctest}
:hide:
>>> from jumpstarter.config import ExporterConfigV1Alpha1DriverInstance
>>> ExporterConfigV1Alpha1DriverInstance.from_path("source/api-reference/drivers/sdwire.yaml").instantiate()
Traceback (most recent call last):
...
FileNotFoundError: failed to find sd-wire device
```

## Client API

The SDWire driver implements the `StorageMuxClient` class, which is a generic
storage class.

```{eval-rst}
.. autoclass:: jumpstarter_driver_opendal.client.StorageMuxClient()
    :members:
```
