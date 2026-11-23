from typing import ClassVar

import yaml
from pydantic import BaseModel, ConfigDict, model_serializer


class JsonBaseModel(BaseModel):
    """A Pydantic BaseModel with additional Jumpstarter JSON options applied."""

    def dump_json(self):
        return self.model_dump_json(indent=4, by_alias=True)

    def dump_yaml(self):
        return yaml.safe_dump(self.model_dump(mode="json", by_alias=True), indent=2)

    model_config = ConfigDict(arbitrary_types_allowed=True, populate_by_name=True)


class OmitNoneModel(JsonBaseModel):
    """A model that leaves its unset optional fields out of what it prints.

    ``omit_none_fields`` names the fields this applies to; when it is not set,
    it applies to every field. A resource then prints the way the cluster
    stores it, without a ``null`` for each field it does not use.
    """

    omit_none_fields: ClassVar[frozenset[str] | None] = None

    @model_serializer(mode="wrap")
    def _omit_none(self, handler):
        data = handler(self)
        names = self.omit_none_fields if self.omit_none_fields is not None else type(self).model_fields.keys()
        for name in names:
            if getattr(self, name) is None:
                data.pop(name, None)
                alias = type(self).model_fields[name].alias
                if alias is not None:
                    data.pop(alias, None)
        return data
