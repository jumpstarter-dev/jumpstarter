import os
from dataclasses import dataclass
from typing import Optional

import yaml
from typing_extensions import Self

from .client import ClientConfig
from .common import CONFIG_API_VERSION, CONFIG_PATH


@dataclass
class UserConfig:
    """The user configuration for the Jumpstarter CLI."""

    # The user config path e.g. ~/.config/jumpstarter
    BASE_CONFIG_PATH = CONFIG_PATH
    USER_CONFIG_PATH = os.path.expanduser(f"{BASE_CONFIG_PATH}/config.yaml")

    CONFIG_KIND = "UserConfig"

    current_client: Optional[ClientConfig]
    """The currently loaded client configuration."""

    def exists() -> bool:
        """Check if a user config exists."""
        return os.path.exists(UserConfig.USER_CONFIG_PATH)

    def load() -> Self:
        """Load the user config from the default path."""
        if UserConfig.exists() is False:
            raise FileNotFoundError(f"User config file not found at '{UserConfig.USER_CONFIG_PATH}'.")

        with open(UserConfig.USER_CONFIG_PATH) as f:
            config: dict = yaml.safe_load(f)

            api_version = config.get("apiVersion")
            kind = config.get("kind")
            config: Optional[dict] = config.get("config", None)

            if api_version != CONFIG_API_VERSION:
                raise ValueError(f"Incorrect config API version {api_version}, expected {CONFIG_API_VERSION}.")
            if kind != UserConfig.CONFIG_KIND:
                raise ValueError(f"Invalid config type {kind}, expected '{UserConfig.CONFIG_KIND}'.")
            if config is None:
                raise ValueError("Config does not contain a 'config' key.")

            current_client_name = config.get("current-client")
            current_client = None

            if current_client_name is not None and current_client_name != "":
                current_client = ClientConfig.load(current_client_name)

            return UserConfig(current_client)

    def load_or_create() -> Self:
        """Check if a user config exists, otherwise create an empty one."""
        if UserConfig.exists() is False:
            if os.path.exists(UserConfig.BASE_CONFIG_PATH) is False:
                os.makedirs(UserConfig.BASE_CONFIG_PATH)
            config = UserConfig(None)
            UserConfig.save(config)
            return config
        # Always return the current user config if it exists
        return UserConfig.load()

    def save(config: Self, path: Optional[str] = None):
        """Save a user config as YAML."""
        value = {
            "apiVersion": CONFIG_API_VERSION,
            "kind": UserConfig.CONFIG_KIND,
            "config": {"current-client": config.current_client.name if config.current_client is not None else ""},
        }

        with open(path or UserConfig.USER_CONFIG_PATH, "w") as f:
            yaml.safe_dump(value, f, sort_keys=False)

    def use_client(self, name: Optional[str]):
        """Updates the current client and saves the user config."""
        if name is not None:
            self.current_client = ClientConfig.load(name)
        else:
            self.current_client = None
        UserConfig.save(self)
