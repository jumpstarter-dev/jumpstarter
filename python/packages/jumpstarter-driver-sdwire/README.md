# SDWire driver

`jumpstarter-driver-sdwire` provides functionality for using the SDWire storage
multiplexer. This device multiplexes an SD card between the DUT and the exporter
host.

## Installation

```{code-block} console
:substitutions:
$ pip3 install --extra-index-url {{index_url}} jumpstarter-driver-sdwire
```

## Configuration

Example configuration:

```{literalinclude} sdwire.yaml
:language: yaml
```

```{doctest}
:hide:
>>> from jumpstarter.config.exporter import ExporterConfigV1Alpha1DriverInstance
>>> ExporterConfigV1Alpha1DriverInstance.from_path("source/api-reference/drivers/sdwire.yaml").instantiate()
Traceback (most recent call last):
...
FileNotFoundError: failed to find sd-wire device
```

## API Reference

The SDWire driver implements the `StorageMuxClient` class, which is a generic
storage class.

```{eval-rst}
.. autoclass:: jumpstarter_driver_opendal.client.StorageMuxClient()
    :members:
```
