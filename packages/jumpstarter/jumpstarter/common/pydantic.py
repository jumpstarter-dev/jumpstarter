import yaml
from pydantic import BaseModel


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
