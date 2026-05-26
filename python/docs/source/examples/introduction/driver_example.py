from abc import ABCMeta, abstractmethod
from collections.abc import Generator
from contextlib import asynccontextmanager
from sys import modules
from types import SimpleNamespace

from anyio import connect_tcp, sleep

from jumpstarter.client import DriverClient
from jumpstarter.common.utils import serve
from jumpstarter.driver import Driver, export, exportstream


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


class GenericDriver(GenericInterface, Driver):
    @export
    def query(self, param: str) -> str:
        return f"Response for {param}"

    @export
    async def async_query(self, param: str) -> str:
        await sleep(1)
        return f"Async response for {param}"

    @export
    def get_data(self) -> Generator[dict, None, None]:
        for i in range(3):
            yield {"type": "data", "value": i, "timestamp": f"2023-04-0{i + 1}"}

    @exportstream
    @asynccontextmanager
    async def create_stream(self):
        async with await connect_tcp(
            remote_host="example.com", remote_port=80
        ) as stream:
            yield stream


class GenericClient(DriverClient):
    def query(self, param: str) -> str:
        return self.call("query", param)

    def async_query(self, param: str) -> str:
        return self.call("async_query", param)

    def get_data(self) -> Generator[dict, None, None]:
        yield from self.streamingcall("get_data")

    def with_stream(self, callback):
        with self.stream("create_stream") as stream:
            callback(stream)


modules["example"] = SimpleNamespace(GenericClient=GenericClient)

if __name__ == "__main__":
    with serve(GenericDriver()) as client:
        assert client.query("test") == "Response for test"
        assert client.async_query("async test") == "Async response for async test"
        data = list(client.get_data())
        assert len(data) == 3
