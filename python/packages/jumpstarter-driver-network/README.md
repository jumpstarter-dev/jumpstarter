# Network Driver

`jumpstarter-driver-network` provides functionality for interacting with network
servers and connections, redirecting DUT network services to the client handling
the lease.

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
      host: 192.168.1.2
      port: 5201
      enable_address: true
```

### Config parameters

| Parameter     | Description                                         | Type  | Required | Default            |
| ------------- | --------------------------------------------------- | ----- | -------- | ------------------ |
| host          | Hostname or IP address of the DUT                   | str   | yes      |                    |
| port          | Port number of the DUT service to connect to        | int   | yes      |                    |
| enable_address | Whether to enable address mode (reporting the DUT host and port to the client) | bool  | no | true   |

`UdpNetwork` takes the same parameters. `UnixNetwork` takes a single required
`path` (str), the Unix domain socket to connect to, and has no address mode.
`EchoNetwork` takes no parameters.

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
