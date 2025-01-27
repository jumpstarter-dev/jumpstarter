import pytest
from asyncclick.testing import CliRunner

from . import exporter
from jumpstarter.config.exporter import ExporterConfigV1Alpha1


@pytest.fixture
def tmp_config_path(tmp_path, monkeypatch):
    monkeypatch.setattr(ExporterConfigV1Alpha1, "BASE_PATH", tmp_path)


@pytest.mark.anyio
async def test_exporter(tmp_config_path):
    runner = CliRunner()

    # create exporter non-interactively
    result = await runner.invoke(
        exporter, ["create-config", "test1", "--endpoint", "example.com:443", "--token", "dummy"]
    )
    assert result.exit_code == 0

    # create duplicate exporter
    result = await runner.invoke(
        exporter, ["create-config", "test1", "--endpoint", "example.com:443", "--token", "dummy"]
    )
    assert result.exit_code != 0

    # create exporter interactively
    result = await runner.invoke(exporter, ["create-config", "test2"], input="example.org:443\ndummytoken\n")
    assert result.exit_code == 0

    # list exporters
    result = await runner.invoke(exporter, ["list-configs"])
    assert result.exit_code == 0
    assert "test1" in result.output
    assert "test2" in result.output

    # delete exporter
    result = await runner.invoke(exporter, ["delete-config", "test2"])
    assert result.exit_code == 0

    ## list exporters
    result = await runner.invoke(exporter, ["list-configs"])
    assert result.exit_code == 0
    assert "test1" in result.output
    assert "test2" not in result.output


@pytest.fixture
def anyio_backend():
    return "asyncio"
