# DBus driver

`jumpstarter-driver-dbus` provides functionality for transparently accessing the dbus on the remote machine.

## Installation

```bash
pip install jumpstarter-driver-dbus
```

## Configuration

Example configuration:

```{literalinclude} dbus.yaml
:language: yaml
```

```{doctest}
:hide:
>>> from jumpstarter.config import ExporterConfigV1Alpha1DriverInstance
>>> ExporterConfigV1Alpha1DriverInstance.from_path("source/api-reference/drivers/dbus.yaml").instantiate()
DbusNetwork(...)
```

## API Reference

```{eval-rst}
.. autoclass:: jumpstarter_driver_network.client.DbusNetworkClient()
    :members:
```

Get machine id of the remote machine

```{doctest}
>>> with dbus:
...     print(subprocess.run([
...         "busctl",
...         "call",
...         "org.freedesktop.systemd1",
...         "/org/freedesktop/systemd1",
...         "org.freedesktop.DBus.Peer",
...         "GetMachineId"
...     ], stdout=subprocess.PIPE).stdout.decode()) # s "34df62c767c846d5a93eb2d6f05d9e1d"
s ...
```

```{testsetup} *
from jumpstarter_driver_network.driver import DbusNetwork
from jumpstarter.common.utils import serve
import subprocess

instance = serve(DbusNetwork(kind="session"))

dbus = instance.__enter__()
```

```{testcleanup} *
instance.__exit__(None, None, None)
```
