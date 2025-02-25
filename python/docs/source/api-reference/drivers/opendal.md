# Opendal driver

The Opendal driver is a driver for interacting with storages attached to the exporter.

## Driver configuration

```{literalinclude} opendal.yaml
:language: yaml
```

```{doctest}
:hide:
>>> from jumpstarter.config import ExporterConfigV1Alpha1DriverInstance
>>> ExporterConfigV1Alpha1DriverInstance.from_path("source/api-reference/drivers/opendal.yaml").instantiate()
Opendal(...)
```

## Client API

```{eval-rst}
.. autoclass:: jumpstarter_driver_opendal.client.OpendalClient()
    :members:
```
