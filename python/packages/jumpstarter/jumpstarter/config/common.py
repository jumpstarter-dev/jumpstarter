import errno
import os
import tempfile
from os import getenv
from pathlib import Path

import yaml
from pydantic_settings import BaseSettings, SettingsConfigDict
from xdg_base_dirs import xdg_config_home

from .env import JMP_CLIENT_CONFIG_HOME

CONFIG_API_VERSION = "jumpstarter.dev/v1alpha1"
CONFIG_PATH = Path(getenv(JMP_CLIENT_CONFIG_HOME, xdg_config_home() / "jumpstarter"))


class ObjectMeta(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="JMP_")

    namespace: str | None
    name: str


def write_yaml_atomically(path: Path, data: dict) -> None:
    """Write `data` to `path` as YAML, replacing the file in one step.

    Another process reading `path` meanwhile sees the old file or the new one, never a truncated or
    interleaved mix. The file is created with mode 600, since client and exporter configs hold tokens.
    """
    temp_fd, temp_path = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        os.fchmod(temp_fd, 0o600)
        with os.fdopen(temp_fd, "w") as f:
            yaml.safe_dump(data, f, sort_keys=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp_path, path)
    finally:
        try:
            os.unlink(temp_path)
        except OSError as e:
            if e.errno != errno.ENOENT:
                raise
