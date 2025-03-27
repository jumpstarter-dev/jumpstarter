from __future__ import annotations

from contextlib import asynccontextmanager, contextmanager, suppress
from pathlib import Path
from typing import Any, ClassVar, Literal, Optional, Self

import grpc
import yaml
from anyio.from_thread import start_blocking_portal
from pydantic import BaseModel, ConfigDict, Field, RootModel

from .common import ObjectMeta
from .grpc import call_credentials
from .tls import TLSConfigV1Alpha1
from jumpstarter.common.grpc import aio_secure_channel, ssl_channel_credentials
from jumpstarter.common.importlib import import_class
from jumpstarter.driver import Driver


class ExporterConfigV1Alpha1DriverInstanceProxy(BaseModel):
    ref: str


class ExporterConfigV1Alpha1DriverInstanceComposite(BaseModel):
    children: dict[str, ExporterConfigV1Alpha1DriverInstance] = Field(default_factory=dict)


class ExporterConfigV1Alpha1DriverInstanceBase(BaseModel):
    type: str
    config: dict[str, Any] = Field(default_factory=dict)
    children: dict[str, ExporterConfigV1Alpha1DriverInstance] = Field(default_factory=dict)


class ExporterConfigV1Alpha1DriverInstance(RootModel):
    root: (
        ExporterConfigV1Alpha1DriverInstanceBase
        | ExporterConfigV1Alpha1DriverInstanceComposite
        | ExporterConfigV1Alpha1DriverInstanceProxy
    )

    def instantiate(self) -> Driver:
        match self.root:
            case ExporterConfigV1Alpha1DriverInstanceBase():
                driver_class = import_class(self.root.type, [], True)

                children = {name: child.instantiate() for name, child in self.root.children.items()}

                return driver_class(children=children, **self.root.config)

            case ExporterConfigV1Alpha1DriverInstanceComposite():
                from jumpstarter_driver_composite.driver import Composite

                children = {name: child.instantiate() for name, child in self.root.children.items()}

                return Composite(children=children)

            case ExporterConfigV1Alpha1DriverInstanceProxy():
                from jumpstarter_driver_composite.driver import Proxy

                return Proxy(ref=self.root.ref)

    @classmethod
    def from_path(cls, path: str) -> ExporterConfigV1Alpha1DriverInstance:
        with open(path) as f:
            return cls.model_validate(yaml.safe_load(f))

    @classmethod
    def from_str(cls, config: str) -> ExporterConfigV1Alpha1DriverInstance:
        return cls.model_validate(yaml.safe_load(config))


class ExporterConfigV1Alpha1(BaseModel):
    BASE_PATH: ClassVar[Path] = Path("/etc/jumpstarter/exporters")

    alias: str = Field(default="default")

    apiVersion: Literal["jumpstarter.dev/v1alpha1"] = Field(default="jumpstarter.dev/v1alpha1")
    kind: Literal["ExporterConfig"] = Field(default="ExporterConfig")
    metadata: ObjectMeta

    endpoint: str
    tls: TLSConfigV1Alpha1 = Field(default_factory=TLSConfigV1Alpha1)
    token: str
    grpcOptions: dict[str, str | int] | None = Field(default_factory=dict)

    export: dict[str, ExporterConfigV1Alpha1DriverInstance] = Field(default_factory=dict)

    path: Path | None = Field(default=None)

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
    def load(cls, alias: str) -> Self:
        config = cls.load_path(cls._get_path(alias))
        config.alias = alias
        return config

    @classmethod
    def list(cls) -> list[Self]:
        exporters = []
        with suppress(FileNotFoundError):
            for entry in cls.BASE_PATH.iterdir():
                exporters.append(cls.load(entry.stem))
        return exporters

    @classmethod
    def dump_yaml(self, config: Self) -> str:
        return yaml.safe_dump(config.model_dump(mode="json", exclude={"alias", "path"}), sort_keys=False)

    @classmethod
    def save(cls, config: Self, path: Optional[str] = None) -> Path:
        # Set the config path before saving
        if path is None:
            config.path = cls._get_path(config.alias)
            config.path.parent.mkdir(parents=True, exist_ok=True)
        else:
            config.path = Path(path)
        with config.path.open(mode="w") as f:
            yaml.safe_dump(config.model_dump(mode="json", exclude={"alias", "path"}), f, sort_keys=False)
        return config.path

    @classmethod
    def delete(cls, alias: str) -> Path:
        path = cls._get_path(alias)
        path.unlink(missing_ok=True)
        return path

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

        async def channel_factory():
            credentials = grpc.composite_channel_credentials(
                await ssl_channel_credentials(self.endpoint, self.tls),
                call_credentials("Exporter", self.metadata, self.token),
            )
            return aio_secure_channel(self.endpoint, credentials, self.grpcOptions)

        async with Exporter(
            channel_factory=channel_factory,
            device_factory=ExporterConfigV1Alpha1DriverInstance(children=self.export).instantiate,
            tls=self.tls,
            grpc_options=self.grpcOptions,
        ) as exporter:
            await exporter.serve()


class ExporterConfigListV1Alpha1(BaseModel):
    api_version: Literal["jumpstarter.dev/v1alpha1"] = Field(alias="apiVersion", default="jumpstarter.dev/v1alpha1")
    items: list[ExporterConfigV1Alpha1]
    kind: Literal["ExporterConfigList"] = Field(default="ExporterConfigList")

    def dump_json(self):
        return self.model_dump_json(indent=4, by_alias=True)

    def dump_yaml(self):
        return yaml.safe_dump(self.model_dump(mode="json", by_alias=True), indent=2)

    model_config = ConfigDict(arbitrary_types_allowed=True, populate_by_name=True)
