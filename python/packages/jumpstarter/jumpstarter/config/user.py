import os
from pathlib import Path
from typing import Annotated, ClassVar, Literal, Optional, Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, PlainSerializer
from pydantic.functional_validators import BeforeValidator

from .client import ClientConfigV1Alpha1
from .common import CONFIG_PATH


def _serialize_current_client(v: ClientConfigV1Alpha1 | None) -> str | None:
    if v:
        return v.alias
    else:
        return None


def _validate_current_client(v: str | ClientConfigV1Alpha1 | None) -> ClientConfigV1Alpha1 | None:
    match v:
        case str():
            try:
                return ClientConfigV1Alpha1.load(v)
            except FileNotFoundError:
                return None
        case ClientConfigV1Alpha1():
            return v
        case None:
            return None
        case _:
            raise ValueError("current-client is not of type str | ClientConfigV1Alpha1 | None")


CurrentClient = Annotated[
    ClientConfigV1Alpha1 | None,
    PlainSerializer(_serialize_current_client),
    BeforeValidator(_validate_current_client),
]


class UserConfigV1Alpha1Config(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    current_client: CurrentClient | None = Field(alias="current-client", default=None)


class UserConfigV1Alpha1(BaseModel):
    """The user configuration for the Jumpstarter CLI."""

    BASE_CONFIG_PATH: ClassVar[Path] = CONFIG_PATH
    USER_CONFIG_PATH: ClassVar[Path] = CONFIG_PATH / "config.yaml"

    apiVersion: Literal["jumpstarter.dev/v1alpha1"] = Field(default="jumpstarter.dev/v1alpha1")
    kind: Literal["UserConfig"] = Field(default="UserConfig")
    config: UserConfigV1Alpha1Config

    @classmethod
    def exists(cls) -> bool:
        """Check if a user config exists."""
        return os.path.exists(cls.USER_CONFIG_PATH)

    @classmethod
    def load(cls) -> Self:
        """Load the user config from the default path."""
        if cls.exists() is False:
            raise FileNotFoundError(f"User config file not found at '{cls.USER_CONFIG_PATH}'.")

        with open(cls.USER_CONFIG_PATH) as f:
            config: dict = yaml.safe_load(f)
            return cls.model_validate(config)

    @classmethod
    def load_or_create(cls) -> Self:
        """Check if a user config exists, otherwise create an empty one."""
        if cls.exists() is False:
            os.makedirs(cls.BASE_CONFIG_PATH, exist_ok=True)
            config = cls(config=UserConfigV1Alpha1Config(current_client=None))
            cls.save(config)
            return config
        # Always return the current user config if it exists
        return cls.load()

    @classmethod
    def save(cls, config: Self, path: Optional[str] = None) -> Path:
        """Save a user config as YAML."""

        with open(path or cls.USER_CONFIG_PATH, "w") as f:
            yaml.safe_dump(config.model_dump(mode="json", by_alias=True), f, sort_keys=False)
        return path or cls.USER_CONFIG_PATH

    def use_client(self, name: Optional[str]) -> Path | None:
        """Updates the current client and saves the user config."""
        if name is not None:
            self.config.current_client = ClientConfigV1Alpha1.load(name)
        else:
            self.config.current_client = None
        self.save(self)
        if self.config.current_client is not None:
            return self.config.current_client.path
        else:
            return None
