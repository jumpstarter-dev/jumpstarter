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

```{eval-rst}
.. autoclass:: jumpstarter_driver_network.adapters.NovncAdapter
    :members:
```

```{eval-rst}
.. autoclass:: jumpstarter_driver_network.adapters.PexpectAdapter
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

Connect to a remote TCP port with a web-based VNC client

```{testcode}
with NovncAdapter(client.tcp_port) as url:
    print(url) # https://novnc.com/noVNC/vnc.html?autoconnect=1&reconnect=1&host=127.0.0.1&port=36459
               # open the url in browser to access the VNC client
```

Interact with a remote TCP port as if it's a serial console

```{testcode}
with PexpectAdapter(client.tcp_port) as expect:
    expect.expect("localhost login:")
    expect.send("root\n")
    expect.expect("Password:")
    expect.send("secret\n")
```
