from typing import Annotated, Literal, Union
from uuid import UUID

from pydantic import BaseModel, Field, Json


class ClientStreamResource(BaseModel):
    kind: Literal["client_stream"] = "client_stream"
    uuid: UUID
    x_jmp_content_encoding: str | None = None


class PresignedRequestResource(BaseModel):
    kind: Literal["presigned_request"] = "presigned_request"
    headers: dict[str, str]
    url: str
    method: Literal["GET", "PUT"]


Resource = Annotated[
    Union[ClientStreamResource, PresignedRequestResource],
    Field(discriminator="kind"),
]


class ResourceMetadata(BaseModel):
    resource: Json[Resource]
    x_jmp_accept_encoding: str | None = None
