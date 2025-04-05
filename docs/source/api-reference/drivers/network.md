# Network drivers

`jumpstarter-driver-network` provides functionality for interacting with network servers and connections.

## Installation

```bash
pip install jumpstarter-driver-network
```

## Configuration

Example configuration:

```yaml
interfaces:
  network:
    driver: jumpstarter_driver_network.NetworkDriver
    parameters:
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
