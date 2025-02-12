from __future__ import annotations

from contextlib import asynccontextmanager, contextmanager, suppress
from pathlib import Path
from typing import Any, ClassVar, Literal, Optional, Self

import grpc
import yaml
from anyio.from_thread import start_blocking_portal
from pydantic import BaseModel, Field

from .common import ObjectMeta
from .grpc import call_credentials
from .tls import TLSConfigV1Alpha1
from jumpstarter.common.grpc import aio_secure_channel, ssl_channel_credentials
from jumpstarter.common.importlib import import_class
from jumpstarter.driver import Driver


class ExporterConfigV1Alpha1DriverInstance(BaseModel):
    type: str = Field(default="jumpstarter_driver_composite.driver.Composite")
    children: dict[str, ExporterConfigV1Alpha1DriverInstance] = Field(default_factory=dict)
    config: dict[str, Any] = Field(default_factory=dict)

    def instantiate(self) -> Driver:
        children = {name: child.instantiate() for name, child in self.children.items()}

        driver_class = import_class(self.type, [], True)

        return driver_class(children=children, **self.config)

    @classmethod
    def from_path(cls, path: str) -> ExporterConfigV1Alpha1DriverInstance:
        with open(path) as f:
            return cls.model_validate(yaml.safe_load(f))


class ExporterConfigV1Alpha1(BaseModel):
    BASE_PATH: ClassVar[Path] = Path("/etc/jumpstarter/exporters")

    alias: str = Field(default="default", exclude=True)

    apiVersion: Literal["jumpstarter.dev/v1alpha1"] = "jumpstarter.dev/v1alpha1"
    kind: Literal["ExporterConfig"] = "ExporterConfig"
    metadata: ObjectMeta

    endpoint: str
    tls: TLSConfigV1Alpha1 = Field(default_factory=TLSConfigV1Alpha1)
    token: str

    export: dict[str, ExporterConfigV1Alpha1DriverInstance] = Field(default_factory=dict)

    path: Path | None = Field(default=None, exclude=True)

    @classmethod
    def _get_path(cls, alias: str):
        return (cls.BASE_PATH / alias).with_suffix(".yaml")

    @classmethod
    def exists(cls, alias: str):
        return cls._get_path(alias).exists()

    @classmethod
    def load_path(cls, path: Path):
        with path.open() as f:
            config = cls.model_validate(yaml.safe_load(f))
            config.path = path
            return config

    @classmethod
    def load(cls, alias: str):
        config = cls.load_path(cls._get_path(alias))
        config.alias = alias
        return config

    @classmethod
    def list(cls):
        exporters = []
        with suppress(FileNotFoundError):
            for entry in cls.BASE_PATH.iterdir():
                exporters.append(cls.load(entry.stem))
        return exporters

    @classmethod
    def dump_yaml(self, config: Self) -> str:
        return yaml.safe_dump(config.model_dump(mode="json"), sort_keys=False)

    @classmethod
    def save(cls, config: Self, path: Optional[str] = None):
        # Set the config path before saving
        if path is None:
            config.path = cls._get_path(config.alias)
            config.path.parent.mkdir(parents=True, exist_ok=True)
        else:
            config.path = Path(path)
        with config.path.open(mode="w") as f:
            yaml.safe_dump(config.model_dump(mode="json"), f, sort_keys=False)

    @classmethod
    def delete(cls, alias: str):
        cls._get_path(alias).unlink(missing_ok=True)

    @asynccontextmanager
    async def serve_unix_async(self):
        # dynamic import to avoid circular imports
        from jumpstarter.exporter import Session

        with Session(
            root_device=ExporterConfigV1Alpha1DriverInstance(children=self.export).instantiate(),
        ) as session:
            async with session.serve_unix_async() as path:
                yield path

    @contextmanager
    def serve_unix(self):
        with start_blocking_portal() as portal:
            with portal.wrap_async_context_manager(self.serve_unix_async()) as path:
                yield path

    async def serve(self):
        # dynamic import to avoid circular imports
        from jumpstarter.exporter import Exporter

        def channel_factory():
            credentials = grpc.composite_channel_credentials(
                ssl_channel_credentials(self.endpoint, self.tls),
                call_credentials("Exporter", self.metadata, self.token),
            )
            return aio_secure_channel(self.endpoint, credentials)

        async with Exporter(
            channel_factory=channel_factory,
            device_factory=ExporterConfigV1Alpha1DriverInstance(children=self.export).instantiate,
            tls=self.tls,
        ) as exporter:
            await exporter.serve()
