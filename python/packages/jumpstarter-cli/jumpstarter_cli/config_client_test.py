from click.testing import CliRunner

from .config_client import config_client
from jumpstarter.config.client import ClientConfigV1Alpha1

CLIENT_ALIAS = "test-client"
CLIENT_NAMESPACE = "default"
CLIENT_NAME = "my-client"
CLIENT_ENDPOINT = "grpc.example.com:443"
CLIENT_TOKEN = "test-token"
CLIENT_ALLOW = "pkg1,pkg2"

COMMON_ARGS = [
    "--namespace",
    CLIENT_NAMESPACE,
    "--name",
    CLIENT_NAME,
    "--endpoint",
    CLIENT_ENDPOINT,
    "--token",
    CLIENT_TOKEN,
    "--allow",
    CLIENT_ALLOW,
]


def test_create_client_config_default(tmp_path):
    out = tmp_path / "client.yaml"
    runner = CliRunner()
    result = runner.invoke(
        config_client,
        ["create", CLIENT_ALIAS, "--out", str(out)] + COMMON_ARGS,
    )
    assert result.exit_code == 0, result.output

    config = ClientConfigV1Alpha1.from_file(out)
    assert config.endpoint == CLIENT_ENDPOINT
    assert config.tls.insecure is False


def test_create_client_config_insecure_tls(tmp_path):
    out = tmp_path / "client.yaml"
    runner = CliRunner()
    result = runner.invoke(
        config_client,
        ["create", CLIENT_ALIAS, "--out", str(out), "--insecure-tls", "--nointeractive"] + COMMON_ARGS,
    )
    assert result.exit_code == 0, result.output

    config = ClientConfigV1Alpha1.from_file(out)
    assert config.tls.insecure is True


def test_create_client_config_insecure_tls_short_flag(tmp_path):
    out = tmp_path / "client.yaml"
    runner = CliRunner()
    result = runner.invoke(
        config_client,
        ["create", CLIENT_ALIAS, "--out", str(out), "-k", "--nointeractive"] + COMMON_ARGS,
    )
    assert result.exit_code == 0, result.output

    config = ClientConfigV1Alpha1.from_file(out)
    assert config.tls.insecure is True


def test_create_client_config_insecure_tls_confirm(tmp_path):
    out = tmp_path / "client.yaml"
    runner = CliRunner()
    result = runner.invoke(
        config_client,
        ["create", CLIENT_ALIAS, "--out", str(out), "--insecure-tls"] + COMMON_ARGS,
        input="y\n",
    )
    assert result.exit_code == 0, result.output

    config = ClientConfigV1Alpha1.from_file(out)
    assert config.tls.insecure is True


def test_create_client_config_insecure_tls_abort(tmp_path):
    out = tmp_path / "client.yaml"
    runner = CliRunner()
    result = runner.invoke(
        config_client,
        ["create", CLIENT_ALIAS, "--out", str(out), "--insecure-tls"] + COMMON_ARGS,
        input="n\n",
    )
    assert result.exit_code != 0
    assert not out.exists()


def _patch_client_list():
    from unittest.mock import patch

    from jumpstarter.config.client import ClientConfigListV1Alpha1
    from jumpstarter.config.common import ObjectMeta

    config = ClientConfigV1Alpha1(
        alias=CLIENT_ALIAS,
        metadata=ObjectMeta(namespace=CLIENT_NAMESPACE, name=CLIENT_NAME),
        endpoint=CLIENT_ENDPOINT,
        token="secret-token",
        refresh_token="secret-refresh-token",
    )
    configs = ClientConfigListV1Alpha1(current_config=CLIENT_ALIAS, items=[config])
    return patch.object(ClientConfigV1Alpha1, "list", return_value=configs)


def test_list_client_configs_redacts_credentials():
    runner = CliRunner()
    with _patch_client_list():
        result = runner.invoke(config_client, ["list", "-o", "json"])
    assert result.exit_code == 0, result.output
    assert CLIENT_ALIAS in result.output
    assert "secret-token" not in result.output
    assert "secret-refresh-token" not in result.output


def test_list_client_configs_show_credentials():
    runner = CliRunner()
    with _patch_client_list():
        result = runner.invoke(config_client, ["list", "-o", "json", "--show-credentials"])
    assert result.exit_code == 0, result.output
    assert "secret-token" in result.output
