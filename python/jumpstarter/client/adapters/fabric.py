from dataclasses import dataclass
from typing import Any

from fabric.config import Config
from fabric.connection import Connection

from .portforward import PortforwardAdapter


@dataclass(kw_only=True)
class FabricAdapter(PortforwardAdapter):
    user: str | None = None
    config: Config | None = None
    forward_agent: bool | None = None
    connect_timeout: int | None = None
    connect_kwargs: dict[str, Any] | None = None
    inline_ssh_env: bool | None = None

    async def __aenter__(self):
        addr = await super().__aenter__()
        return Connection(
            addr[0],
            user=self.user,
            port=addr[1],
            config=self.config,
            forward_agent=self.forward_agent,
            connect_timeout=self.connect_timeout,
            connect_kwargs=self.connect_kwargs,
            inline_ssh_env=self.inline_ssh_env,
        )
