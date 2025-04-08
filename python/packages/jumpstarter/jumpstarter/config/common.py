from os import getenv
from pathlib import Path

from pydantic import BaseModel
from xdg_base_dirs import xdg_config_home

from .env import JMP_CLIENT_CONFIG_HOME

CONFIG_API_VERSION = "jumpstarter.dev/v1alpha1"
CONFIG_PATH = Path(getenv(JMP_CLIENT_CONFIG_HOME, xdg_config_home() / "jumpstarter"))


class ObjectMeta(BaseModel):
    namespace: str | None
    name: str
