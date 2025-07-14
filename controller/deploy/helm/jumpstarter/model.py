#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["pydantic"]
# ///

from __future__ import annotations

import json

from typing import Any, Dict, Optional, Union

from pydantic import BaseModel, ConfigDict, Field


class Metrics(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: Optional[bool] = Field(
        None, description="Whether to enable metrics exporting and service"
    )


class Global(BaseModel):
    model_config = ConfigDict(extra="forbid")

    namespace: Optional[str] = Field(
        None, description="Namespace where the components will be deployed"
    )
    timestamp: Optional[Union[int, str]] = Field(
        None,
        description="Timestamp to be used to trigger a new deployment, i.e. if you want pods to be restarted and pickup the latest tag",
    )
    baseDomain: Optional[str] = Field(
        None, description="Base domain to construct the FQDN for the service endpoints"
    )
    storageClassName: Optional[str] = Field(
        None, description="Storage class name for multiple reader/writer PVC"
    )
    storageClassNameRWM: Optional[str] = Field(
        None, description="Storage class name for the PVCs"
    )
    metrics: Optional[Metrics] = None


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid")

    jumpstarter_controller: Optional[Dict[str, Any]] = Field(
        None, alias="jumpstarter-controller"
    )
    global_: Optional[Global] = Field(None, alias="global")


print(json.dumps(Model.model_json_schema(), indent=2))
