# VNC Driver

`jumpstarter-driver-vnc` provides functionality for interacting with VNC servers. It allows you to create a secure, tunneled VNC session in your browser.

## Installation

```shell
pip3 install --extra-index-url https://pkg.jumpstarter.dev/simple/ jumpstarter-driver-vnc
```

## Configuration

The VNC driver is a composite driver that requires a TCP child driver to establish the underlying network connection. The TCP driver should be configured to point to the VNC server's host and port, which is often `127.0.0.1` from the perspective of the Jumpstarter server.

Example `exporter.yaml` configuration:

```{literalinclude} ../../../../../packages/jumpstarter-driver-vnc/examples/config.yaml
:language: yaml
```

## API Reference

```{eval-rst}
.. autoclass:: jumpstarter_driver_vnc.driver.Vnc()
```
