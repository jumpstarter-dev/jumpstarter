import pytest
from asyncclick.testing import CliRunner

from . import client


@pytest.mark.anyio
async def test_client():
    runner = CliRunner()

    # create client non-interactively
    result = await runner.invoke(
        client,
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
            "--allow",
            "jumpstarter.*",
        ],
    )
    assert result.exit_code == 0

    # create duplicate client
    result = await runner.invoke(
        client,
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
            "--allow",
            "jumpstarter.*",
        ],
    )
    assert result.exit_code != 0

    # create client interactively
    result = await runner.invoke(
        client,
        ["config", "create", "test2"],
        input="default\ntest2\nexample.org:443\ndummytoken\njumpstarter.*,com.example.*\n",
    )
    assert result.exit_code == 0

    # list clients
    result = await runner.invoke(client, ["config", "list"])
    assert result.exit_code == 0
    assert "*         test1   example.com:443" in result.output
    assert "          test2   example.org:443" in result.output

    # set default client
    result = await runner.invoke(client, ["config", "use", "test2"])
    assert result.exit_code == 0

    # list clients
    result = await runner.invoke(client, ["config", "list"])
    assert result.exit_code == 0
    assert "          test1   example.com:443" in result.output
    assert "*         test2   example.org:443" in result.output

    # delete default client
    result = await runner.invoke(client, ["config", "delete", "test2"])
    assert result.exit_code == 0

    # list clients
    result = await runner.invoke(client, ["config", "list"])
    assert result.exit_code == 0
    assert "*         test1   example.com:443" in result.output
    assert "*         test2   example.org:443" not in result.output


@pytest.fixture
def anyio_backend():
    return "asyncio"
