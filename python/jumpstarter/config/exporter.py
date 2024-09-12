from __future__ import annotations

from contextlib import asynccontextmanager, suppress
from pathlib import Path
from typing import ClassVar, Literal

import grpc
import yaml
from pydantic import BaseModel, Field

from jumpstarter.common.importlib import import_class
from jumpstarter.driver import Driver
from jumpstarter.exporter import Exporter, Session

from .common import Metadata


class ExporterConfigV1Alpha1DriverInstance(BaseModel):
    type: str = Field(default="jumpstarter.drivers.composite.driver.Composite")
    children: dict[str, ExporterConfigV1Alpha1DriverInstance] = Field(default_factory=dict)
    config: dict[str, str | int | float] = Field(default_factory=dict)

    def instantiate(self) -> Driver:
        children = {name: child.instantiate() for name, child in self.children.items()}

        driver_class = import_class(self.type, [], True)

        return driver_class(children=children, **self.config)


class ExporterConfigV1Alpha1(BaseModel):
    BASE_PATH: ClassVar[Path] = Path("/etc/jumpstarter/exporters")

    alias: str = Field(default="default", exclude=True)

    apiVersion: Literal["jumpstarter.dev/v1alpha1"] = "jumpstarter.dev/v1alpha1"
    kind: Literal["ExporterConfig"] = "ExporterConfig"
    metadata: Metadata

    endpoint: str
    token: str

    export: dict[str, ExporterConfigV1Alpha1DriverInstance] = Field(default_factory=dict)

    @property
    def path(self):
        return self.__path(self.alias)

    @classmethod
    def __path(cls, alias: str):
        return (cls.BASE_PATH / alias).with_suffix(".yaml")

    @classmethod
    def load_path(cls, path: Path):
        with path.open() as f:
            config = cls.model_validate(yaml.safe_load(f))
            return config

    @classmethod
    def load(cls, alias: str):
        config = cls.load_path(cls.__path(alias))
        config.alias = alias
        return config

    @classmethod
    def list(cls):
        exporters = []
        with suppress(FileNotFoundError):
            for entry in cls.BASE_PATH.iterdir():
                exporters.append(cls.load(entry.stem))
        return exporters

    def save(self, delete=False):
        if delete:
            self.path.unlink(missing_ok=True)
        else:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open(mode="w") as f:
                yaml.safe_dump(self.model_dump(mode="json"), f, sort_keys=False)

    @asynccontextmanager
    async def serve_unix_async(self):
        session = Session(
            root_device=ExporterConfigV1Alpha1DriverInstance(children=self.export).instantiate(),
        )
        async with session.serve_unix_async() as path:
            yield path

    async def serve(self):
        credentials = grpc.composite_channel_credentials(
            grpc.local_channel_credentials(),  # FIXME: use ssl_channel_credentials
            grpc.access_token_call_credentials(self.token),
        )

        async with grpc.aio.secure_channel(self.endpoint, credentials) as channel:
            async with Exporter(
                channel=channel,
                device_factory=ExporterConfigV1Alpha1DriverInstance(children=self.export).instantiate,
            ) as exporter:
                await exporter.serve()
