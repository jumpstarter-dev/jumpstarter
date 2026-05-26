# SD Wire Driver

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

```{literalinclude} ../../../../../packages/jumpstarter-driver-sdwire/examples/config.yaml
:language: yaml
```

## API Reference

The SDWire driver implements the `StorageMuxClient` class, which is a generic
storage class.

```{eval-rst}
.. autoclass:: jumpstarter_driver_opendal.client.StorageMuxClient()
    :members:
```
