from os import getenv
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

from .env import JMP_CLIENT_CONFIG_HOME
from jumpstarter.common.xdg import xdg_config_home

CONFIG_API_VERSION = "jumpstarter.dev/v1alpha1"
CONFIG_PATH = Path(getenv(JMP_CLIENT_CONFIG_HOME, xdg_config_home() / "jumpstarter"))


class ObjectMeta(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="JMP_")

    namespace: str | None
    name: str
