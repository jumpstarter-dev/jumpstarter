from __future__ import annotations

from importlib import import_module
from typing import Literal

import grpc
from pydantic import BaseModel, Field

from jumpstarter.driver import Driver
from jumpstarter.exporter import Exporter


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
    apiVersion: Literal["jumpstarter.dev/v1alpha1"]
    kind: Literal["ExporterConfig"]

    endpoint: str
    token: str

    export: ExporterConfigV1Alpha1DriverInstance

    async def serve(self):
        credentials = grpc.composite_channel_credentials(
            grpc.local_channel_credentials(),  # FIXME: use ssl_channel_credentials
            grpc.access_token_call_credentials(self.token),
        )

        async with grpc.aio.secure_channel(self.endpoint, credentials) as channel:
            async with Exporter(
                channel=channel,
                device_factory=self.export.instantiate,
            ) as exporter:
                await exporter.serve()
