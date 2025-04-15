from pydantic import ConfigDict

from jumpstarter.common.pydantic import SerializableBaseModel


class JsonBaseModel(SerializableBaseModel):
    """A Pydantic BaseModel with additional Jumpstarter JSON options applied."""

    def dump_name(self):
        return "{}.jumpstarter.dev/{}\n".format(self.kind.lower(), self.metadata.name)

    model_config = ConfigDict(arbitrary_types_allowed=True, populate_by_name=True)
