# Ustreamer driver

`jumpstarter-driver-ustreamer` provides functionality for using the ustreamer
video streaming server driven by the jumpstarter exporter. This driver takes a
video device and exposes both snapshot and streaming interfaces.

## Installation

```shell
pip3 install --extra-index-url https://pkg.jumpstarter.dev/simple/ jumpstarter-driver-ustreamer
```

## Configuration

Example configuration:

```{literalinclude} ustreamer.yaml
:language: yaml
```

```{doctest}
:hide:
>>> from jumpstarter.config.exporter import ExporterConfigV1Alpha1DriverInstance
>>> ExporterConfigV1Alpha1DriverInstance.from_path("source/reference/package-apis/drivers/ustreamer.yaml").instantiate()
Traceback (most recent call last):
...
io.UnsupportedOperation: fileno
```

## API Reference

```{eval-rst}
.. autoclass:: jumpstarter_driver_ustreamer.client.UStreamerClient()
    :members:
```
