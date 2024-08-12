from abc import ABCMeta, abstractmethod
from collections.abc import AsyncGenerator, Generator

import click
from pydantic import BaseModel

from jumpstarter.drivers import Driver, DriverClient, export


class PowerReading(BaseModel):
    voltage: float
    current: float

    @property
    def apparent_power(self):
        return self.voltage * self.current


class PowerInterface(metaclass=ABCMeta):
    @classmethod
    def client(cls) -> str:
        return "jumpstarter.drivers.power.PowerClient"

    @abstractmethod
    async def on(self) -> str: ...

    @abstractmethod
    async def off(self) -> str: ...

    @abstractmethod
    async def read(self) -> AsyncGenerator[PowerReading, None]: ...


class PowerClient(DriverClient):
    def on(self) -> str:
        return self.call("on")

    def off(self) -> str:
        return self.call("off")

    def read(self) -> Generator[PowerReading, None, None]:
        for v in self.streamingcall("read"):
            yield PowerReading.model_validate(v, strict=True)

    def cli(self):
        @click.group
        def base():
            """Generic power"""
            pass

        @base.command()
        def on():
            """Power on"""
            click.echo(self.on())

        @base.command()
        def off():
            """Power off"""
            click.echo(self.off())

        return base


class MockPower(PowerInterface, Driver):
    @export
    async def on(self) -> str:
        return "ok"

    @export
    async def off(self) -> str:
        return "ok"

    @export
    async def read(self) -> AsyncGenerator[PowerReading, None]:
        yield PowerReading(voltage=0.0, current=0.0)
        yield PowerReading(voltage=5.0, current=2.0)


class SyncMockPower(PowerInterface, Driver):
    @export
    def on(self) -> str:
        return "ok"

    @export
    def off(self) -> str:
        return "ok"

    @export
    def read(self) -> AsyncGenerator[PowerReading, None]:
        yield PowerReading(voltage=0.0, current=0.0)
        yield PowerReading(voltage=5.0, current=2.0)
