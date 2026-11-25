"""Find Jumpstarter client and exporter configs by alias or path, like ``jmp`` does."""

from __future__ import annotations

import os
from pathlib import Path

from jumpstarter.config.client import ClientConfigV1Alpha1
from jumpstarter.config.exporter import ExporterConfigV1Alpha1
from jumpstarter.config.user import UserConfigV1Alpha1

ENV_CONFIG = "env"


def is_path_like(value: str) -> bool:
    return os.sep in value or "/" in value or value.endswith((".yaml", ".yml"))


def _resolve_path(value: str, base_dir: Path | None) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute() and base_dir is not None:
        path = base_dir / path
    return path


def load_client_config(value: str | None = None, *, base_dir: Path | None = None) -> ClientConfigV1Alpha1:
    """Load the client config to lease with, by the rules ``jmp`` uses.

    ``value`` is an alias from ``jmp config client list``, a path to a client
    config YAML, or ``env`` to build one from ``JMP_ENDPOINT``/``JMP_TOKEN``.
    Without a value, the environment's client config is used if it is
    complete, else the current client of ``jmp config client use``.
    """
    if value == ENV_CONFIG:
        return ClientConfigV1Alpha1.from_env()
    if value:
        if is_path_like(value):
            return ClientConfigV1Alpha1.from_file(_resolve_path(value, base_dir))
        return ClientConfigV1Alpha1.load(value)
    config = ClientConfigV1Alpha1.try_from_env() or UserConfigV1Alpha1.load_or_create().config.current_client
    if config is None:
        raise FileNotFoundError("no client config: give one, or choose one with `jmp config client use`")
    return config


def load_exporter_config(value: str, *, base_dir: Path | None = None) -> ExporterConfigV1Alpha1:
    """Load a local exporter config by alias (``jmp config exporter list``) or path."""
    if is_path_like(value):
        return ExporterConfigV1Alpha1.load_path(_resolve_path(value, base_dir))
    return ExporterConfigV1Alpha1.load(value)
