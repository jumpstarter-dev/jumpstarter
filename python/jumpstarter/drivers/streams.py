from typing import Annotated, Literal, Union
from uuid import UUID

from pydantic import BaseModel, Field, TypeAdapter


class ResourceStreamRequest(BaseModel):
    kind: Literal["resource"] = "resource"
    uuid: UUID


class DriverStreamRequest(BaseModel):
    kind: Literal["driver"] = "driver"
    uuid: UUID
    method: str


StreamRequest = TypeAdapter(
    Annotated[
        Union[ResourceStreamRequest, DriverStreamRequest],
        Field(discriminator="kind"),
    ]
)
