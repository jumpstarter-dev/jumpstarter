# uStreamer Driver

`jumpstarter-driver-ustreamer` provides functionality for using the ustreamer
video streaming server driven by the jumpstarter exporter. This driver takes a
video device and exposes both snapshot and streaming interfaces.

## Installation

```{code-block} console
:substitutions:
$ pip3 install --extra-index-url {{index_url}} jumpstarter-driver-ustreamer
```

## Configuration

Example configuration:

```{literalinclude} ../../../../../packages/jumpstarter-driver-ustreamer/examples/config.yaml
:language: yaml
```

## API Reference

```{eval-rst}
.. autoclass:: jumpstarter_driver_ustreamer.client.UStreamerClient()
    :members:
```
