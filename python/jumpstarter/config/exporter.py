from __future__ import annotations

from contextlib import asynccontextmanager
from importlib import import_module
from pathlib import Path
from typing import ClassVar, Literal

import grpc
import yaml
from pydantic import BaseModel, Field

from jumpstarter.driver import Driver
from jumpstarter.exporter import Exporter, Session


class ExporterConfigV1Alpha1DriverInstance(BaseModel):
    type: str = Field(default="jumpstarter.drivers.composite.driver.Composite")
    children: dict[str, ExporterConfigV1Alpha1DriverInstance] = Field(default_factory=dict)
    config: dict[str, str | int | float] = Field(default_factory=dict)

    def instantiate(self) -> Driver:
        children = {name: child.instantiate() for name, child in self.children.items()}

        # reference: https://docs.djangoproject.com/en/5.0/_modules/django/utils/module_loading/#import_string
        module_path, class_name = self.type.rsplit(".", 1)
        driver_class = getattr(import_module(module_path), class_name)

        return driver_class(children=children, **self.config)


class ExporterConfigV1Alpha1(BaseModel):
    BASE_PATH: ClassVar[Path] = Path("/etc/jumpstarter/exporters")

    apiVersion: Literal["jumpstarter.dev/v1alpha1"]
    kind: Literal["ExporterConfig"]

    endpoint: str
    token: str

    export: dict[str, ExporterConfigV1Alpha1DriverInstance]

    @classmethod
    def load(cls, name: str):
        path = (cls.BASE_PATH / name).with_suffix(".yaml")
        with path.open() as f:
            return cls.model_validate(yaml.safe_load(f))

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
