from click.testing import CliRunner

from .run import run


def test_run_help():
    """`jmp run` is wired and documents its options."""
    result = CliRunner().invoke(run, ["--help"])
    assert result.exit_code == 0
    assert "Run an exporter locally." in result.output


def test_run_requires_config():
    result = CliRunner().invoke(run, [])
    assert result.exit_code != 0
    assert "exporter-config" in result.output


def test_run_rejects_standalone_listener(tmp_path):
    """Standalone TCP-listener mode is owned by the Rust core, not the Python entrypoint yet."""
    cfg = tmp_path / "exporter.yaml"
    cfg.write_text(
        "apiVersion: jumpstarter.dev/v1alpha1\nkind: ExporterConfig\n"
        "metadata:\n  namespace: default\n  name: test\n"
        "endpoint: example.com:443\ntoken: t\nexport: {}\n"
    )
    result = CliRunner().invoke(run, ["--exporter-config", str(cfg), "--tls-grpc-listener", "1234"])
    assert result.exit_code != 0
    assert "Standalone listener mode" in result.output
