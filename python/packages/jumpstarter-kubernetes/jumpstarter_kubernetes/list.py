from typing import Literal

import yaml
from pydantic import BaseModel, Field


class V1Alpha1List[T](BaseModel):
    """A generic list result type."""

    api_version: Literal["jumpstarter.dev/v1alpha1"] = Field(alias="apiVersion", default="jumpstarter.dev/v1alpha1")
    items: list[T]
    kind: Literal["List"] = Field(default="List")

    def dump_json(self):
        return self.model_dump_json(indent=4, by_alias=True)

    def dump_yaml(self):
        return yaml.safe_dump(self.model_dump(by_alias=True), indent=2)
