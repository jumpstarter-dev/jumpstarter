import pytest
from asyncclick.testing import CliRunner

from . import jmp


@pytest.mark.anyio
async def test_cli():
    runner = CliRunner()
    result = await runner.invoke(jmp, [])
    assert "admin" in result.output
    assert "client" in result.output
    assert "exporter" in result.output
    assert "version" in result.output


@pytest.fixture
def anyio_backend():
    return "asyncio"
