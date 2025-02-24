from pydantic import BaseModel


class PresignedRequest(BaseModel):
    url: str
    method: str
    headers: dict[str, str]
