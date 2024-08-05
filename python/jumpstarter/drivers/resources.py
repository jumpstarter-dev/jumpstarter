from typing import Annotated, Literal, Union
from uuid import UUID

from pydantic import BaseModel, Field, TypeAdapter


class ClientStreamResource(BaseModel):
    kind: Literal["client_stream"] = "client_stream"
    uuid: UUID


class PresignedRequestResource(BaseModel):
    kind: Literal["presigned_request"] = "presigned_request"
    headers: dict[str, str]
    url: str
    method: str


Resource = TypeAdapter(
    Annotated[
        Union[ClientStreamResource, PresignedRequestResource],
        Field(discriminator="kind"),
    ]
)
