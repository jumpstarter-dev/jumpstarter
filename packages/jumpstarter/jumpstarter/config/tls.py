from pydantic import BaseModel, Field


class TLSConfigV1Alpha1(BaseModel):
    ca: str = Field(default="")
    insecure: bool = Field(default=True)
    # TODO(mangelajo): Move this back to false once we have a proper way to setup
    # TLS certificates in the jumpstarter controller.
