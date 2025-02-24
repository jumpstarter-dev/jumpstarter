# Network adapters

Network adapters are for transforming network connections exposed by drivers

```{eval-rst}
.. autofunction:: jumpstarter_driver_network.adapters.TcpPortforwardAdapter
```

```{eval-rst}
.. autofunction:: jumpstarter_driver_network.adapters.UnixPortforwardAdapter
```

```{eval-rst}
.. autofunction:: jumpstarter_driver_network.adapters.NovncAdapter
```

```{eval-rst}
.. autofunction:: jumpstarter_driver_network.adapters.PexpectAdapter
```

```{eval-rst}
.. autofunction:: jumpstarter_driver_network.adapters.FabricAdapter
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

### Forward a remote TCP port to a local TCP port

```{doctest}
>>> from jumpstarter_driver_network.adapters import TcpPortforwardAdapter
>>> # random port on localhost
>>> with TcpPortforwardAdapter(client=network.tcp_port) as addr:
...     print(addr[0], addr[1])
127.0.0.1 ...
>>>
>>> # specific address and port
>>> with TcpPortforwardAdapter(client=network.tcp_port, local_host="127.0.0.2", local_port=8080) as addr:
...     print(addr[0], addr[1])
127.0.0.2 8080

```

### Forward a remote Unix domain socket to a local socket

```{doctest}
>>> from jumpstarter_driver_network.adapters import UnixPortforwardAdapter
>>> with UnixPortforwardAdapter(client=network.unix_socket) as addr:
...     print(addr)
/tmp/jumpstarter-.../socket
>>> # the type of the remote socket and the local one doesn't have to match
>>> # e.g. forward a remote Unix domain socket to a local TCP port
>>> with TcpPortforwardAdapter(client=network.unix_socket) as addr:
...     print(addr[0], addr[1])
127.0.0.1 ...

```

Connect to a remote TCP port with a web-based VNC client

```{doctest}
>>> from jumpstarter_driver_network.adapters import NovncAdapter
>>> with NovncAdapter(client=network.tcp_port) as url:
...     print(url) # open the url in browser to access the VNC client
https://novnc.com/noVNC/vnc.html?autoconnect=1&reconnect=1&host=127.0.0.1&port=...

```

Interact with a remote TCP port as if it's a serial console

See [pexpect](https://pexpect.readthedocs.io/en/stable/api/fdpexpect.html) for API documentation

```{doctest}
>>> from jumpstarter_driver_network.adapters import PexpectAdapter
>>> # the server echos all inputs
>>> with PexpectAdapter(client=network.tcp_port) as expect:
...     assert expect.send("hello") == 5 # written 5 bytes
...     assert expect.expect(["hi", "hello"]) == 1 # found string at index 1

```

Connect to a remote TCP port with the fabric SSH client

See [fabric](https://docs.fabfile.org/en/latest/api/connection.html#fabric.connection.Connection) for API documentation

```{testcode}
:skipif: True
with FabricAdapter(client=client.tcp_port, connect_kwargs={"password": "secret"}) as conn:
    conn.run("uname")
```
