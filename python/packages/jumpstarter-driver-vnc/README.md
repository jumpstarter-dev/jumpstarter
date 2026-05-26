# VNC Driver

`jumpstarter-driver-vnc` provides functionality for interacting with VNC servers. It allows you to create a secure, tunneled VNC session in your browser.

## Installation

```shell
pip3 install --extra-index-url https://pkg.jumpstarter.dev/simple/ jumpstarter-driver-vnc
```

## Configuration

The VNC driver is a composite driver that requires a TCP child driver to establish the underlying network connection. The TCP driver should be configured to point to the VNC server's host and port, which is often `127.0.0.1` from the perspective of the Jumpstarter server.

Example `exporter.yaml` configuration:

```yaml
export:
  vnc:
    type: jumpstarter_driver_vnc.driver.Vnc
    # You can set the default encryption behavior for the `j vnc session` command.
    # If not set, it defaults to False (unencrypted).
    default_encrypt: false
    children:
      tcp:
        type: jumpstarter_driver_network.driver.TcpNetwork
        config:
          host: "127.0.0.1"
          port: 5901 # Default VNC port for display :1
```

## API Reference

```{eval-rst}
.. autoclass:: jumpstarter_driver_vnc.driver.Vnc()
```
