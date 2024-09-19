import pytest
from click.testing import CliRunner

from jumpstarter.config.exporter import ExporterConfigV1Alpha1

from .exporter import exporter


@pytest.fixture
def tmp_config_path(tmp_path, monkeypatch):
    monkeypatch.setattr(ExporterConfigV1Alpha1, "BASE_PATH", tmp_path)


def test_exporter(tmp_config_path):
    runner = CliRunner()

    # create exporter non-interactively
    assert (
        runner.invoke(exporter, ["create", "test1", "--endpoint", "example.com:443", "--token", "dummy"]).exit_code == 0
    )

    # create duplicate exporter
    assert (
        runner.invoke(exporter, ["create", "test1", "--endpoint", "example.com:443", "--token", "dummy"]).exit_code != 0
    )

    # create exporter interactively
    assert runner.invoke(exporter, ["create", "test2"], input="example.org:443\ndummytoken\n").exit_code == 0

    # list exporters
    result = runner.invoke(exporter, ["list"])
    assert result.exit_code == 0
    assert "test1" in result.output
    assert "test2" in result.output

    # delete exporter
    assert runner.invoke(exporter, ["delete", "test2"]).exit_code == 0

    ## list exporters
    result = runner.invoke(exporter, ["list"])
    assert result.exit_code == 0
    assert "test1" in result.output
    assert "test2" not in result.output
