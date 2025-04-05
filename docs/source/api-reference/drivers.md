# Driver Classes and Architecture

## Driver Architecture

Jumpstarter uses a client/server model for drivers to interact with hardware.
The architecture follows a pattern with these key components:

- **Interface Class** - An abstract base class using Python's ABCMeta to define
  the contract (methods and their signatures) that driver implementations must
  fulfill. The interface also specifies the client class through the `client()`
  class method.

- **Driver Class** - Inherits from both the Interface and the base `Driver`
  class, implementing the logic to configure and use hardware interfaces. Driver
  methods are marked with the `@export` decorator to expose them over the
  network.

- **Driver Client** - Provides a user-friendly interface that can be used by
  clients to interact with the driver either locally or remotely over the
  network.

When a client requests a lease and connects to an exporter, a session is created
for all tests the client needs to execute. Within this session, the specified
`Driver` subclass is instantiated for each configured interface. These driver
instances live throughout the session's duration, maintaining state and
executing setup/teardown logic.

On the client side, a `DriverClient` subclass is instantiated for each exported
interface. Since clients may run on different machines than exporters,
`DriverClient` classes are loaded dynamically when specified in the allowed
packages list.

To maintain compatibility, avoid making breaking changes to interfaces. Add new
methods when needed but preserve existing signatures. If breaking changes are
required, create new interface, client, and driver versions within the same
module.

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

## Example Implementation
```{testcode}
from sys import modules
from types import SimpleNamespace
from anyio import connect_tcp, sleep
from contextlib import asynccontextmanager
from collections.abc import Generator, AsyncGenerator
from abc import ABCMeta, abstractmethod
from jumpstarter.driver import Driver, export, exportstream
from jumpstarter.client import DriverClient
from jumpstarter.common.utils import serve

# Define an interface with ABCMeta
class GenericInterface(metaclass=ABCMeta):
    @classmethod
    def client(cls) -> str:
        return "example.GenericClient"

    @abstractmethod
    def query(self, param: str) -> str: ...

    @abstractmethod
    def get_data(self) -> Generator[dict, None, None]: ...

    @abstractmethod
    def create_stream(self): ...

# Implement the interface with the Driver base class
class GenericDriver(GenericInterface, Driver):
    @export
    def query(self, param: str) -> str:
        # This could be any device-specific command
        return f"Response for {param}"

    # driver calls can be either sync or async
    @export
    async def async_query(self, param: str) -> str:
        # Example of an async operation with delay
        await sleep(1)
        return f"Async response for {param}"

    @export
    def get_data(self) -> Generator[dict, None, None]:
        # Example of a streaming response - could be sensor data, logs, etc.
        for i in range(3):
            yield {"type": "data", "value": i, "timestamp": f"2023-04-0{i+1}"}

    # stream constructor has to be an AsyncContextManager
    # that yield an anyio.abc.ObjectStream
    @exportstream
    @asynccontextmanager
    async def create_stream(self):
        # This could be any stream connection to a device
        async with await connect_tcp(remote_host="example.com", remote_port=80) as stream:
            yield stream

class GenericClient(DriverClient):
    # client methods are sync
    def query(self, param: str) -> str:
        return self.call("query", param)

    def async_query(self, param: str) -> str:
        # async driver methods can be invoked the same way
        return self.call("async_query", param)

    def get_data(self) -> Generator[dict, None, None]:
        yield from self.streamingcall("get_data")

    # Streams can be used for bidirectional communication
    def with_stream(self, callback):
        with self.stream("create_stream") as stream:
            callback(stream)

modules["example"] = SimpleNamespace(GenericClient=GenericClient)

with serve(GenericDriver()) as client:
    print(client.query("test"))
    assert client.async_query("async test") == "Async response for async test"
    data = list(client.get_data())
    assert len(data) == 3
```

```{testoutput}
Response for test
```