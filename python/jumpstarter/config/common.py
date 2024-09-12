from pathlib import Path

from pydantic import BaseModel

CONFIG_API_VERSION = "jumpstarter.dev/v1alpha1"
CONFIG_PATH = Path.home() / ".config" / "jumpstarter"


class Metadata(BaseModel):
    name: str
    namespace: str
