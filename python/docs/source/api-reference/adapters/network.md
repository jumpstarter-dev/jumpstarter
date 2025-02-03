# Network adapters

Network adapters are for transforming network connections exposed by drivers

```{eval-rst}
.. autoclass:: jumpstarter_driver_network.adapters.TcpPortforwardAdapter
    :members:
```

```{eval-rst}
.. autoclass:: jumpstarter_driver_network.adapters.UnixPortforwardAdapter
    :members:
```

## Examples
```yaml
export:
  tcp_port:
    type: "jumpstarter_driver_network.driver.TcpNetwork"
    config:
      host: localhost
      port: 80
  unix_socket:
    type: "jumpstarter_driver_network.driver.UnixNetwork"
    config:
      path: /tmp/test.sock
```

Forward a remote TCP port to a local TCP port

```{testcode}
# random port on localhost
with TcpPortforwardAdapter(client.tcp_port) as addr:
    print(addr[0], addr[1]) # 127.0.0.1 38406

# specific address and port
with TcpPortforwardAdapter(client.tcp_port, local_host="192.0.2.1", local_port=8080) as addr:
    print(addr[0], addr[1]) # 192.0.2.1 8080
```

Forward a remote Unix domain socket to a local socket

```{testcode}
with UnixPortforwardAdapter(client.unix_socket) as addr:
    print(addr) # /tmp/jumpstarter-w30wxu64/socket

# the type of the remote socket and the local one doesn't have to match
# e.g. forward a remote Unix domain socket to a local TCP port
with TcpPortforwardAdapter(client.unix_socket) as addr:
    print(addr[0], addr[1]) # 127.0.0.1 38406
```
