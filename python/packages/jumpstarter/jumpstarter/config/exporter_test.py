from pathlib import Path

import pytest

from .common import ObjectMeta
from .exporter import ExporterConfigV1Alpha1, ExporterConfigV1Alpha1DriverInstance
from .tls import TLSConfigV1Alpha1
from jumpstarter.common.exceptions import ConfigurationError

pytestmark = pytest.mark.anyio


def test_exporter_config(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setattr(ExporterConfigV1Alpha1, "BASE_PATH", tmp_path)

    path = tmp_path / "test.yaml"

    text = """apiVersion: jumpstarter.dev/v1alpha1
kind: ExporterConfig
metadata:
  namespace: default
  name: test
tls:
  ca: "cacertificatedata"
  insecure: true
endpoint: "jumpstarter.my-lab.com:1443"
token: "dGhpc2lzYXRva2VuLTEyMzQxMjM0MTIzNEyMzQtc2Rxd3Jxd2VycXdlcnF3ZXJxd2VyLTEyMzQxMjM0MTIz"

export:
  power:
    type: "jumpstarter_driver_power.driver.PduPower"
    config:
      host: "192.168.1.111"
      port: 1234
      auth:
          username: "admin"
          password: "secret"
  serial:
    type: "jumpstarter_driver_pyserial.driver.Pyserial"
    config:
      port: "/dev/ttyUSB0"
      baudrate: 115200
  nested:
    children:
      custom:
        type: "vendorpackage.CustomDriver"
        config:
          hello: "world"
"""
    path.write_text(
        text,
        encoding="utf-8",
    )

    config = ExporterConfigV1Alpha1.load("test")

    assert config == ExporterConfigV1Alpha1(
        alias="test",
        apiVersion="jumpstarter.dev/v1alpha1",
        kind="ExporterConfig",
        metadata=ObjectMeta(namespace="default", name="test"),
        endpoint="jumpstarter.my-lab.com:1443",
        token="dGhpc2lzYXRva2VuLTEyMzQxMjM0MTIzNEyMzQtc2Rxd3Jxd2VycXdlcnF3ZXJxd2VyLTEyMzQxMjM0MTIz",
        tls=TLSConfigV1Alpha1(ca="cacertificatedata", insecure=True),
        export={
            "power": ExporterConfigV1Alpha1DriverInstance(
                type="jumpstarter_driver_power.driver.PduPower",
                config={
                    "host": "192.168.1.111",
                    "port": 1234,
                    "auth": {
                        "username": "admin",
                        "password": "secret",
                    },
                },
            ),
            "serial": ExporterConfigV1Alpha1DriverInstance(
                type="jumpstarter_driver_pyserial.driver.Pyserial",
                config={
                    "port": "/dev/ttyUSB0",
                    "baudrate": 115200,
                },
            ),
            "nested": ExporterConfigV1Alpha1DriverInstance(
                children={
                    "custom": ExporterConfigV1Alpha1DriverInstance(
                        type="vendorpackage.CustomDriver",
                        children={},
                        config={
                            "hello": "world",
                        },
                    )
                },
            ),
        },
        config={},
        path=path,
    )

    path.unlink()

    ExporterConfigV1Alpha1.save(config)

    assert config == ExporterConfigV1Alpha1.load("test")


def test_exporter_config_with_hooks(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setattr(ExporterConfigV1Alpha1, "BASE_PATH", tmp_path)

    path = tmp_path / "test-hooks.yaml"

    text = """apiVersion: jumpstarter.dev/v1alpha1
kind: ExporterConfig
metadata:
  namespace: default
  name: test-hooks
endpoint: "jumpstarter.my-lab.com:1443"
token: "test-token"
hooks:
  beforeLease:
    script: |
      echo "Pre-lease hook for $LEASE_NAME"
      j power on
    timeout: 600
  afterLease:
    script: |
      echo "Post-lease hook for $LEASE_NAME"
      j power off
    timeout: 600
export:
  power:
    type: "jumpstarter_driver_power.driver.PduPower"
"""
    path.write_text(
        text,
        encoding="utf-8",
    )

    config = ExporterConfigV1Alpha1.load("test-hooks")

    assert config.hooks.before_lease.script == 'echo "Pre-lease hook for $LEASE_NAME"\nj power on\n'
    assert config.hooks.after_lease.script == 'echo "Post-lease hook for $LEASE_NAME"\nj power off\n'

    # Test that it round-trips correctly
    path.unlink()
    ExporterConfigV1Alpha1.save(config)
    reloaded_config = ExporterConfigV1Alpha1.load("test-hooks")

    assert reloaded_config.hooks.before_lease.script == config.hooks.before_lease.script
    assert reloaded_config.hooks.after_lease.script == config.hooks.after_lease.script

    # Test that the YAML uses camelCase
    yaml_output = ExporterConfigV1Alpha1.dump_yaml(config)
    assert "beforeLease:" in yaml_output
    assert "afterLease:" in yaml_output
    assert "before_lease:" not in yaml_output
    assert "after_lease:" not in yaml_output


def _write_minimal_config(path: Path, name: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"""apiVersion: jumpstarter.dev/v1alpha1
kind: ExporterConfig
metadata:
  namespace: default
  name: {name}
endpoint: "jumpstarter.my-lab.com:1443"
token: "test-token"
""",
        encoding="utf-8",
    )


def test_exporter_config_system_fallback(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """A system-location config is found; saving shadows it in the user dir."""
    user_path = tmp_path / "user"
    system_path = tmp_path / "system"
    monkeypatch.setattr(ExporterConfigV1Alpha1, "BASE_PATH", user_path)
    monkeypatch.setattr(ExporterConfigV1Alpha1, "SYSTEM_CONFIG_PATH", system_path)

    # A config that only exists in the legacy system location is still found.
    _write_minimal_config((system_path / "legacy.yaml"), "legacy")

    assert ExporterConfigV1Alpha1.exists("legacy")
    legacy = ExporterConfigV1Alpha1.load("legacy")
    assert legacy.alias == "legacy"
    assert legacy.path == system_path / "legacy.yaml"
    assert {e.alias for e in ExporterConfigV1Alpha1.list().items} == {"legacy"}

    # Saving defaults to the user dir, never touching the system location.
    ExporterConfigV1Alpha1.save(legacy)
    assert (user_path / "legacy.yaml").exists()
    # The user-dir copy now shadows the system one.
    assert ExporterConfigV1Alpha1.load("legacy").path == user_path / "legacy.yaml"


def test_exporter_config_user_shadows_system(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """The user config takes precedence and list() de-duplicates aliases."""
    user_path = tmp_path / "user"
    system_path = tmp_path / "system"
    monkeypatch.setattr(ExporterConfigV1Alpha1, "BASE_PATH", user_path)
    monkeypatch.setattr(ExporterConfigV1Alpha1, "SYSTEM_CONFIG_PATH", system_path)

    _write_minimal_config((system_path / "dup.yaml"), "dup")
    _write_minimal_config((user_path / "dup.yaml"), "dup")

    # User dir takes precedence and list() does not return duplicates.
    assert ExporterConfigV1Alpha1.load("dup").path == user_path / "dup.yaml"
    aliases = [e.alias for e in ExporterConfigV1Alpha1.list().items]
    assert aliases == ["dup"]


def test_delete_system_only_refuses(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """delete() raises ConfigurationError when the alias exists only in the system location."""
    user_path = tmp_path / "user"
    system_path = tmp_path / "system"
    monkeypatch.setattr(ExporterConfigV1Alpha1, "BASE_PATH", user_path)
    monkeypatch.setattr(ExporterConfigV1Alpha1, "SYSTEM_CONFIG_PATH", system_path)

    _write_minimal_config(system_path / "sys.yaml", "sys")

    with pytest.raises(ConfigurationError, match="system location"):
        ExporterConfigV1Alpha1.delete("sys")

    assert (system_path / "sys.yaml").exists()


def test_delete_both_locations_removes_only_user(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """delete() removes the user-dir copy and leaves the system-location copy intact."""
    user_path = tmp_path / "user"
    system_path = tmp_path / "system"
    monkeypatch.setattr(ExporterConfigV1Alpha1, "BASE_PATH", user_path)
    monkeypatch.setattr(ExporterConfigV1Alpha1, "SYSTEM_CONFIG_PATH", system_path)

    _write_minimal_config(user_path / "both.yaml", "both")
    _write_minimal_config(system_path / "both.yaml", "both")

    ExporterConfigV1Alpha1.delete("both")

    assert not (user_path / "both.yaml").exists()
    assert (system_path / "both.yaml").exists()


def test_delete_nonexistent_is_noop(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """delete() is a no-op when the alias does not exist in either location."""
    user_path = tmp_path / "user"
    system_path = tmp_path / "system"
    monkeypatch.setattr(ExporterConfigV1Alpha1, "BASE_PATH", user_path)
    monkeypatch.setattr(ExporterConfigV1Alpha1, "SYSTEM_CONFIG_PATH", system_path)

    returned = ExporterConfigV1Alpha1.delete("ghost")
    assert returned == ExporterConfigV1Alpha1._get_path("ghost")


def test_list_merges_user_and_system(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """list() returns configs from both user and system dirs when aliases differ."""
    user_path = tmp_path / "user"
    system_path = tmp_path / "system"
    monkeypatch.setattr(ExporterConfigV1Alpha1, "BASE_PATH", user_path)
    monkeypatch.setattr(ExporterConfigV1Alpha1, "SYSTEM_CONFIG_PATH", system_path)

    _write_minimal_config(user_path / "alpha.yaml", "alpha")
    _write_minimal_config(system_path / "beta.yaml", "beta")

    aliases = {e.alias for e in ExporterConfigV1Alpha1.list().items}
    assert aliases == {"alpha", "beta"}


@pytest.mark.parametrize("bad_alias", ["/tmp/evil", "../evil", "a/b", "a\\b", ".", "..", ""])
def test_alias_path_traversal_rejected(bad_alias: str):
    """Aliases with path separators or dot-segments are rejected before any filesystem access."""
    with pytest.raises(ConfigurationError, match="Invalid exporter alias"):
        ExporterConfigV1Alpha1.validate_alias(bad_alias)
