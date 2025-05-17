# Network drivers

`jumpstarter-driver-network` provides functionality for interacting with network
servers and connections.

## Installation

```{code-block} console
:substitutions:
$ pip3 install --extra-index-url {{index_url}} jumpstarter-driver-network
```

## Configuration

Example configuration:

```yaml
export:
  network:
    type: jumpstarter_driver_network.driver.TcpNetwork
    config:
      # Add required parameters here
```

## API Reference

Network driver classes:

```{eval-rst}
.. autoclass:: jumpstarter_driver_network.driver.TcpNetwork()
```

```{eval-rst}
.. autoclass:: jumpstarter_driver_network.driver.UdpNetwork()
```

```{eval-rst}
.. autoclass:: jumpstarter_driver_network.driver.UnixNetwork()
```

```{eval-rst}
.. autoclass:: jumpstarter_driver_network.driver.EchoNetwork()
```

Client API:

```{eval-rst}
.. autoclass:: jumpstarter_driver_network.client.NetworkClient()
    :members:
```
