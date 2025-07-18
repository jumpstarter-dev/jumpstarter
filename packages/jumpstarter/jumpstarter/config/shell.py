from pydantic import BaseModel


class ShellConfigV1Alpha1(BaseModel):
    use_profiles: bool = False
