import yaml
from pydantic import BaseModel, ConfigDict


class JsonBaseModel(BaseModel):
    """A Pydantic BaseModel with additional Jumpstarter JSON options applied."""

    def dump_json(self):
        return self.model_dump_json(indent=4, by_alias=True)

    def dump_yaml(self):
        return yaml.safe_dump(self.model_dump(mode="json", by_alias=True), indent=2)

    model_config = ConfigDict(arbitrary_types_allowed=True, populate_by_name=True)
