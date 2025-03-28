import pytest
from asyncclick.testing import CliRunner

from . import jmp


@pytest.mark.anyio
async def test_cli():
    runner = CliRunner()
    result = await runner.invoke(jmp, [])
    for subcommand in [
        "config",
        "create",
        "delete",
        "pkg",
        "get",
        "login",
        "run",
        "shell",
        "update",
        "version",
    ]:
        assert subcommand in result.output


@pytest.fixture
def anyio_backend():
    return "asyncio"
