from pydantic import ConfigDict

from jumpstarter.common.pydantic import OutputMode, OutputType, SerializableBaseModel


class JsonBaseModel(SerializableBaseModel):
    """A Pydantic BaseModel with additional Jumpstarter JSON options applied."""

    def dump(self, mode: OutputType = None):
        match mode:
            case OutputMode.NAME:
                return self.dump_name()
            case _:
                return super().dump(mode)

    def dump_name(self):
        return "{}.jumpstarter.dev/{}".format(self.kind.lower(), self.metadata.name)

    model_config = ConfigDict(arbitrary_types_allowed=True, populate_by_name=True)
