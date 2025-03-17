from dataclasses import field
from uuid import UUID, uuid4

from pydantic.dataclasses import dataclass


@dataclass(kw_only=True, slots=True)
class Metadata:
    uuid: UUID = field(default_factory=uuid4)
    labels: dict[str, str] = field(default_factory=dict)

    @property
    def name(self):
        return self.labels.get("jumpstarter.dev/name", "unknown")
