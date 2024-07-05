from dataclasses import dataclass, field
from uuid import UUID, uuid4


@dataclass(kw_only=True, slots=True)
class DeviceMeta:
    uuid: UUID = field(default_factory=uuid4)
    labels: dict[str, str] = field(default_factory=dict)

    def __post_init__(self):
        # TODO: validate meta
        pass
