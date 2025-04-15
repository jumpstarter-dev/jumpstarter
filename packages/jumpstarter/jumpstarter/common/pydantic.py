from typing import Generic, Literal, TypeVar

import yaml
from pydantic import BaseModel, Field

T = TypeVar("T")


class OutputMode(str):
    JSON = "json"
    YAML = "yaml"
    NAME = "name"
    PATH = "path"


OutputType = OutputMode | None


class SerializableBaseModel(BaseModel):
    def dump(self, mode: OutputType = None):
        match mode:
            case OutputMode.JSON:
                return self.dump_json()
            case OutputMode.YAML:
                return self.dump_yaml()
            case OutputMode.NAME:
                return self.dump_name()
            case OutputMode.PATH:
                return self.dump_path()
            case None | _:
                raise NotImplementedError("unimplemented output mode: {}".format(mode))

    def dump_json(self) -> str:
        return self.model_dump_json(indent=4, by_alias=True) + "\n"

    def dump_yaml(self) -> str:
        return yaml.safe_dump(self.model_dump(mode="json", by_alias=True), indent=2)


class SerializableBaseModelList(SerializableBaseModel, Generic[T]):
    api_version: Literal["jumpstarter.dev/v1alpha1"] = Field(
        alias="apiVersion",
        default="jumpstarter.dev/v1alpha1",
    )
    kind: Literal["List"] = Field(default="List")
    items: list[T]

    def dump_name(self):
        return "".join(item.dump_name() for item in self.items)
