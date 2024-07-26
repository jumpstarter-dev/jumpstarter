# API reference

## Driver
```{eval-rst}
.. autoclass:: jumpstarter.drivers.Driver
    :members:
.. autodecorator:: jumpstarter.drivers.export
.. autodecorator:: jumpstarter.drivers.exportstream
```

## Driver Client
```{eval-rst}
.. autoclass:: jumpstarter.drivers.DriverClient
    :members:
```

### Mixins
```{eval-rst}
.. automodule:: jumpstarter.drivers.mixins
    :members:
```

## Example
```python
from anyio import connect_tcp, sleep
from contextlib import contextmanager
from collections.abc import Generator
from jumpstarter.drivers import Driver, DriverClient, export, exportstream
from jumpstarter.drivers.mixins import StreamMixin

class ExampleDriver(Driver)
    @classmethod
    def client(cls) -> str:
        return "example.ExampleClient"

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

class ExampleClient(DriverClient, StreamMixin):
    # client methods are sync
    def echo(self, message) -> str:
        return self.call("echo", message)
        # async driver methods can be invoked the same way
        # return self.call("echo_async", message)

    def echo_generator(self, message) -> Generator[str, None, None]:
        yield from self.streamingcall("echo_generator"):
```
