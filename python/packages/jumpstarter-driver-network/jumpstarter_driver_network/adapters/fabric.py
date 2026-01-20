from contextlib import asynccontextmanager
from functools import partial
from typing import Any

from fabric.config import Config
from fabric.connection import Connection

from .portforward import handler
from jumpstarter.client import DriverClient
from jumpstarter.client.adapters import blocking
from jumpstarter.common import TemporaryTcpListener


@blocking
@asynccontextmanager
async def FabricAdapter(
    *,
    client: DriverClient,
    method: str = "connect",
    user: str | None = None,
    config: Config | None = None,
    forward_agent: bool | None = None,
    connect_timeout: int | None = None,
    connect_kwargs: dict[str, Any] | None = None,
    inline_ssh_env: bool | None = None,
):
    async with TemporaryTcpListener(partial(handler, client, method)) as addr:
        yield Connection(
            addr[0],
            user=user,
            port=addr[1],
            config=config,
            forward_agent=forward_agent,
            connect_timeout=connect_timeout,
            connect_kwargs=connect_kwargs,
            inline_ssh_env=inline_ssh_env,
        )
