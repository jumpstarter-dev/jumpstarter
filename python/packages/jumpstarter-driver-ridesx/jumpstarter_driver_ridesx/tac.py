"""Shared TAC/EPM serial helpers for RideSX drivers."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence

CommandDelay = tuple[str, float]
PROMPT = b"CMD >> "


async def send_power_command(serial, logger, command: str) -> None:
    """Send a power command and wait for ok."""
    async with serial.connect() as stream:
        logger.info(f"Executing power command: {command}")
        await stream.send(f"{command}\r".encode())
        data = b""
        while b"ok" not in data:
            chunk = await stream.receive()
            data += chunk
        logger.debug(f"Command {command} acknowledged with 'ok'")


async def send_power_commands_sequence(serial, logger, commands: Sequence[CommandDelay]) -> None:
    """Send a sequence of power commands with delays."""
    for command, delay in commands:
        await send_power_command(serial, logger, command)
        if delay > 0:
            await asyncio.sleep(delay)


async def send_tac_command(tac, command: str) -> None:
    """Send a TAC command and wait for ok."""
    async with tac.connect() as stream:
        await stream.send(f"{command}\r".encode())
        data = b""
        while b"ok" not in data:
            chunk = await stream.receive()
            data += chunk


async def send_tac_sequence(tac, commands: Sequence[CommandDelay]) -> None:
    """Send a sequence of TAC commands with delays."""
    for command, delay in commands:
        await send_tac_command(tac, command)
        if delay > 0:
            await asyncio.sleep(delay)
