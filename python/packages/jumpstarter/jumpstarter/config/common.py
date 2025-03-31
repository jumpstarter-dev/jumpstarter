from pydantic import BaseModel
from xdg_base_dirs import xdg_config_home

CONFIG_API_VERSION = "jumpstarter.dev/v1alpha1"
CONFIG_PATH = xdg_config_home() / "jumpstarter"


class ObjectMeta(BaseModel):
    namespace: str | None
    name: str
