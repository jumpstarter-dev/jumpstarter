import yaml
from pydantic import BaseModel, ConfigDict


class JsonBaseModel(BaseModel):
    """
    A Pydantic `BaseModel` with additional Jumpstarter JSON options applied.

    This configures the default `dump_json` and `dump_yaml` methods for the model and
    enables arbitrary types to allow non-model types to be represented.

    Examples:
        Basic usage with a simple Pydantic model:

        ```python
        from jumpstarter.models import JsonBaseModel

        class User(JsonBaseModel):
            name: str
            email: str
    """

    def dump_json(self):
        return self.model_dump_json(indent=4, by_alias=True)

    def dump_yaml(self):
        return yaml.safe_dump(self.model_dump(mode="json", by_alias=True), indent=2)

    model_config = ConfigDict(arbitrary_types_allowed=True, populate_by_name=True)
