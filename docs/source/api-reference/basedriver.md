# Base Driver classes

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

## Example
```{testcode}
>>> from sys import modules
>>> from types import SimpleNamespace
>>> from anyio import connect_tcp, sleep
>>> from contextlib import asynccontextmanager
>>> from collections.abc import Generator
>>> from jumpstarter.driver import Driver, export, exportstream
>>> from jumpstarter.client import DriverClient
>>> from jumpstarter.common.utils import serve
>>> 
>>> class ExampleDriver(Driver):
...     @classmethod
...     def client(cls) -> str:
...         return f"example.ExampleClient"
... 
...     @export
...     def echo(self, message) -> str:
...         return message
... 
...     # driver calls can be either sync or async
...     @export
...     async def echo_async(self, message) -> str:
...         await sleep(5)
...         return message
... 
...     @export
...     def echo_generator(self, message) -> Generator[str, None, None]:
...         for _ in range(10):
...             yield message
... 
...     # stream constructor has to be an AsyncContextManager
...     # that yield an anyio.abc.ObjectStream
...     @exportstream
...     @asynccontextmanager
...     async def connect_tcp(self):
...         async with await connect_tcp(remote_host="example.com", remote_port=80) as stream:
...             yield stream
>>> 
>>> class ExampleClient(DriverClient):
...     # client methods are sync
...     def echo(self, message) -> str:
...         return self.call("echo", message)
...         # async driver methods can be invoked the same way
...         # return self.call("echo_async", message)
... 
...     def echo_generator(self, message) -> Generator[str, None, None]:
...         yield from self.streamingcall("echo_generator", message)
>>> 
>>> modules["example"] = SimpleNamespace(ExampleClient=ExampleClient)
>>> 
>>> with serve(ExampleDriver()) as client:
...     print(client.echo("hello"))
...     assert list(client.echo_generator("hello")) == ["hello"] * 10
hello

```
