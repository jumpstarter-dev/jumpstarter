# Ustreamer driver

The Ustreamer driver is a driver for using the ustreamer video streaming server
driven by the jumpstarter exporter. This driver takes a video device and
exposes both snapshot and streaming interfaces.

## Driver configuration

```{literalinclude} ustreamer.yaml
:language: yaml
```

```{doctest}
:hide:
>>> from jumpstarter.config import ExporterConfigV1Alpha1DriverInstance
>>> ExporterConfigV1Alpha1DriverInstance.from_path("source/api-reference/drivers/ustreamer.yaml").instantiate()
Traceback (most recent call last):
...
io.UnsupportedOperation: fileno
```

## Client API

```{eval-rst}
.. autoclass:: jumpstarter_driver_ustreamer.client.UStreamerClient()
    :members:
```
