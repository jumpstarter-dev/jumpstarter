from pydantic import BaseModel, Field


class TLSConfigV1Alpha1(BaseModel):
    ca: str = Field(default="")
    insecure: bool = Field(default=False)
