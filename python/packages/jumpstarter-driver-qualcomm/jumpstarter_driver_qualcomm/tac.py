"""TAC serial helpers for Qualcomm SoC control."""

from __future__ import annotations

import asyncio

from .soc_profiles import CommandDelay


async def send_tac_command(tac, command: str) -> None:
    async with tac.connect() as stream:
        await stream.send(f"{command}\r".encode())
        data = b""
        while b"ok" not in data:
            chunk = await stream.receive()
            data += chunk


async def send_tac_sequence(tac, commands: list[CommandDelay]) -> None:
    for command, delay in commands:
        await send_tac_command(tac, command)
        if delay > 0:
            await asyncio.sleep(delay)
