import pytest
from asyncclick.testing import CliRunner

from . import exporter


@pytest.mark.anyio
async def test_exporter():
    runner = CliRunner()

    # create exporter non-interactively
    result = await runner.invoke(
        exporter,
        [
            "config",
            "create",
            "test1",
            "--namespace",
            "default",
            "--name",
            "test1",
            "--endpoint",
            "example.com:443",
            "--token",
            "dummy",
        ],
    )
    assert result.exit_code == 0

    # create duplicate exporter
    result = await runner.invoke(
        exporter,
        [
            "config",
            "create",
            "test1",
            "--namespace",
            "default",
            "--name",
            "test1",
            "--endpoint",
            "example.com:443",
            "--token",
            "dummy",
        ],
    )
    assert result.exit_code != 0

    # create exporter interactively
    result = await runner.invoke(
        exporter, ["config", "create", "test2"], input="default\ntest2\nexample.org:443\ndummytoken\n"
    )
    assert result.exit_code == 0

    # list exporters
    result = await runner.invoke(exporter, ["config", "list"])
    assert result.exit_code == 0
    assert "test1" in result.output
    assert "test2" in result.output

    # delete exporter
    result = await runner.invoke(exporter, ["config", "delete", "test2"])
    assert result.exit_code == 0

    ## list exporters
    result = await runner.invoke(exporter, ["config", "list"])
    assert result.exit_code == 0
    assert "test1" in result.output
    assert "test2" not in result.output


@pytest.fixture
def anyio_backend():
    return "asyncio"
