# Vnc Driver

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

The client class for this driver is `jumpstarter_driver_vnc.client.VNClient`.

### `vnc.session()`

This asynchronous context manager establishes a connection to the remote VNC server and provides a local web server to view the session.

**Usage:**

```python
async with vnc.session() as novnc_adapter:
    print(f"VNC session available at: {novnc_adapter.url}")
    # The session remains open until the context block is exited.
    await novnc_adapter.wait()
```

### CLI: `j vnc session`

This driver provides a convenient CLI command within the `jmp shell`. By default, it will open the session URL in your default web browser.

**Usage:**

```shell
# This will start the local server and open a browser.
j vnc session

# To prevent it from opening a browser automatically:
j vnc session --no-browser

# To force an encrypted (wss://) or unencrypted (ws://) connection, overriding
# the default set in the exporter configuration:
j vnc session --encrypt
j vnc session --no-encrypt
```

> **Note:** Using an encrypted connection is intended for advanced scenarios where the local proxy can be configured with a TLS certificate that your browser trusts. For standard local development, modern browsers will likely reject the self-signed certificate and the connection will fail.
