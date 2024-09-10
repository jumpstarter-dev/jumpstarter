from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class ExporterConfigV1Alpha1DriverInstance(BaseModel):
    type: str = Field(default="jumpstarter.drivers.composite.driver.Composite")
    children: dict[str, ExporterConfigV1Alpha1DriverInstance] = Field(default_factory=dict)
    config: dict[str, str | int | float] = Field(default_factory=dict)


class ExporterConfigV1Alpha1(BaseModel):
    apiVersion: Literal["jumpstarter.dev/v1alpha1"]
    kind: Literal["Exporter"]

    endpoint: str
    token: str

    export: ExporterConfigV1Alpha1DriverInstance
