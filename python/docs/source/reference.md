# API Reference

```{warning}
This project is still evolving, so these docs may be incomplete or out-of-date.
```

## Driver
```{eval-rst}
.. autoclass:: jumpstarter.driver.Driver
    :members:
.. autodecorator:: jumpstarter.driver.export
.. autodecorator:: jumpstarter.driver.exportstream
```

## Driver Client
```{eval-rst}
.. autoclass:: jumpstarter.client.DriverClient
    :members:
```

### Adapters
```{eval-rst}
.. automodule:: jumpstarter.client.adapters
    :members:
```

## Example
```{testsetup} *
from types import SimpleNamespace
import importlib

real_import_module = importlib.import_module

def import_module(name, package=None):
    if name == "example":
        return SimpleNamespace(ExampleClient=globals()["ExampleClient"])
    else:
        return real_import_module(name, package)

importlib.import_module = import_module
```

```{testcode}
from anyio import connect_tcp, sleep
from contextlib import asynccontextmanager
from collections.abc import Generator
from jumpstarter.driver import Driver, export, exportstream
from jumpstarter.client import DriverClient
from jumpstarter.common.utils import serve

class ExampleDriver(Driver):
    @classmethod
    def client(cls) -> str:
        return f"example.ExampleClient"

    @export
    def echo(self, message) -> str:
        return message

    # driver calls can be either sync or async
    @export
    async def echo_async(self, message) -> str:
        await sleep(5)
        return message

    @export
    def echo_generator(self, message) -> Generator[str, None, None]:
        for _ in range(10):
            yield message

    # stream constructor has to be an AsyncContextManager
    # that yield an anyio.abc.ObjectStream
    @exportstream
    @asynccontextmanager
    async def connect_tcp(self):
        async with await connect_tcp(remote_host="example.com", remote_port=80) as stream:
            yield stream

class ExampleClient(DriverClient):
    # client methods are sync
    def echo(self, message) -> str:
        return self.call("echo", message)
        # async driver methods can be invoked the same way
        # return self.call("echo_async", message)

    def echo_generator(self, message) -> Generator[str, None, None]:
        yield from self.streamingcall("echo_generator", message)

with serve(ExampleDriver()) as client:
    print(client.echo("hello"))
    assert list(client.echo_generator("hello")) == ["hello"] * 10
```

```{testoutput}
hello
```
