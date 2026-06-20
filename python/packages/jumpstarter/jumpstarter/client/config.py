"""Client connection config — a thin, pydantic-free replacement for the old
``ClientConfigV1Alpha1`` / ``UserConfigV1Alpha1`` models.

The Rust core owns config parsing/serialization (``jumpstarter_core.parse_yaml`` /
``dump_yaml``) and the lease lifecycle (the FFI ``Lease`` shim over ``ControllerSession``).
This module is just the stdlib glue the remaining Python consumer (``jumpstarter-testing``)
needs: resolve which client config to use (an explicit alias, the ``JUMPSTARTER_*`` env, or
the user config's ``current-client``), read its connection fields, write a refreshed token
back, and open a lease.
"""

from __future__ import annotations

import json
import os
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path

from jumpstarter.common.exceptions import FileNotFoundError
from jumpstarter.common.xdg import xdg_config_home
from jumpstarter.config.env import (
    JMP_CLIENT_CONFIG_HOME,
    JMP_ENDPOINT,
    JMP_LEASE,
    JMP_NAME,
    JMP_NAMESPACE,
    JMP_TOKEN,
)


def _config_home() -> Path:
    return Path(os.getenv(JMP_CLIENT_CONFIG_HOME) or (xdg_config_home() / "jumpstarter"))


def _clients_dir() -> Path:
    return _config_home() / "clients"


def _user_config_path() -> Path:
    return _config_home() / "config.yaml"


def _parse_yaml(text: str) -> dict:
    import jumpstarter_core as jc

    return json.loads(jc.parse_yaml(text))


def _drivers(allow: list[str], unsafe: bool) -> tuple[list[str], bool]:
    return allow, (unsafe or "UNSAFE" in allow)


@dataclass
class ClientConnection:
    """The fields needed to reach the controller and acquire a lease for one client identity."""

    endpoint: str | None
    namespace: str
    name: str
    token: str | None = None
    refresh_token: str | None = None
    ca: str = ""
    insecure: bool = False
    allow: list[str] = field(default_factory=list)
    unsafe: bool = False
    acquisition_timeout: int = 7200
    alias: str | None = None
    path: Path | None = None

    # ---- loading --------------------------------------------------------

    @classmethod
    def from_dict(cls, data: dict, *, alias: str | None = None, path: Path | None = None) -> ClientConnection:
        meta = data.get("metadata") or {}
        tls = data.get("tls") or {}
        drivers = data.get("drivers") or {}
        leases = data.get("leases") or {}
        allow, unsafe = _drivers(list(drivers.get("allow") or []), bool(drivers.get("unsafe", False)))
        return cls(
            endpoint=data.get("endpoint"),
            namespace=meta.get("namespace") or "default",
            name=meta.get("name") or (alias or ""),
            token=data.get("token"),
            refresh_token=data.get("refresh_token"),
            ca=tls.get("ca") or "",
            insecure=bool(tls.get("insecure", False)),
            allow=allow,
            unsafe=unsafe,
            acquisition_timeout=int(leases.get("acquisition_timeout", 7200)),
            alias=alias,
            path=path,
        )

    @classmethod
    def from_path(cls, path: os.PathLike) -> ClientConnection:
        path = Path(path)
        data = _parse_yaml(path.read_text())
        return cls.from_dict(data, alias=path.stem, path=path)

    @classmethod
    def load(cls, alias: str) -> ClientConnection:
        """Load a client config by alias from the user config dir."""
        path = _clients_dir() / f"{alias}.yaml"
        if not path.exists():
            raise FileNotFoundError(f"Client config '{path}' does not exist.")
        return cls.from_path(path)

    @classmethod
    def from_env(cls) -> ClientConnection | None:
        """Build a connection from ``JUMPSTARTER_*`` env vars, or None if no endpoint is set."""
        endpoint = os.environ.get(JMP_ENDPOINT)
        if not endpoint:
            return None
        return cls(
            endpoint=endpoint,
            namespace=os.environ.get(JMP_NAMESPACE) or "default",
            name=os.environ.get(JMP_NAME) or "",
            token=os.environ.get(JMP_TOKEN),
        )

    @classmethod
    def _current_client_alias(cls) -> str | None:
        user_path = _user_config_path()
        if not user_path.exists():
            return None
        data = _parse_yaml(user_path.read_text())
        return ((data.get("config") or {}).get("current-client")) or None

    @classmethod
    def resolve(cls, alias: str | None = None) -> ClientConnection:
        """Resolve the client config to use: an explicit alias, else the ``JUMPSTARTER_*`` env,
        else the user config's ``current-client``."""
        if alias is not None:
            return cls.load(alias)
        env = cls.from_env()
        if env is not None:
            return env
        current = cls._current_client_alias()
        if current is None:
            raise FileNotFoundError(
                "No jumpstarter client config found. Run 'jmp config client use <name>' or set "
                "JUMPSTARTER_* environment variables."
            )
        return cls.load(current)

    # ---- token persistence ---------------------------------------------

    def save_token(self, token: str, refresh_token: str | None = None) -> None:
        """Persist a refreshed token (and optional refresh token) back to the config file,
        preserving every other field (re-serialized by the Rust core)."""
        import jumpstarter_core as jc

        if self.path is None:
            raise ValueError("cannot save a token: this connection has no on-disk path")
        data = _parse_yaml(Path(self.path).read_text())
        data["token"] = token
        if refresh_token is not None:
            data["refresh_token"] = refresh_token
        Path(self.path).write_text(jc.dump_yaml(json.dumps(data)))
        self.token = token
        if refresh_token is not None:
            self.refresh_token = refresh_token

    # ---- lease ----------------------------------------------------------

    @asynccontextmanager
    async def lease_async(
        self,
        selector: str | None = None,
        exporter_name: str | None = None,
        lease_name: str | None = None,
        duration: timedelta = timedelta(minutes=30),
        portal=None,
        acquisition_timeout: timedelta | None = None,
    ):
        from jumpstarter.client import Lease

        lease_name = lease_name or os.environ.get(JMP_LEASE, "")
        release_lease = lease_name == ""
        timeout_seconds = (
            int(acquisition_timeout.total_seconds()) if acquisition_timeout is not None else self.acquisition_timeout
        )
        async with Lease(
            endpoint=self.endpoint,
            namespace=self.namespace,
            token=self.token,
            ca=self.ca,
            insecure=self.insecure,
            client_name=self.name,
            name=lease_name,
            selector=selector,
            requested_exporter_name=exporter_name,
            duration=duration,
            portal=portal,
            allow=self.allow,
            unsafe=self.unsafe,
            release=release_lease,
            acquisition_timeout=timeout_seconds,
        ) as lease:
            yield lease

    @contextmanager
    def lease(
        self,
        selector: str | None = None,
        exporter_name: str | None = None,
        lease_name: str | None = None,
        duration: timedelta = timedelta(minutes=30),
    ):
        from anyio.from_thread import start_blocking_portal

        with start_blocking_portal() as portal:
            with portal.wrap_async_context_manager(
                self.lease_async(selector, exporter_name, lease_name, duration, portal)
            ) as lease:
                yield lease
