from dataclasses import dataclass, field
from uuid import UUID, uuid4


@dataclass(kw_only=True, slots=True)
class Metadata:
    uuid: UUID = field(default_factory=uuid4)
    labels: dict[str, str] = field(default_factory=dict)

    def __post_init__(self):
        if "jumpstarter.dev/name" not in self.labels:
            raise ValueError("missing required label: jumpstarter.dev/name")


@dataclass(kw_only=True, slots=True)
class MetadataFilter:
    labels: dict[str, str] = field(default_factory=dict)
