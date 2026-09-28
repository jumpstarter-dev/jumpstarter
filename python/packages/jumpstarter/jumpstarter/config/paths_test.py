import os
from pathlib import PurePosixPath, PureWindowsPath
from types import SimpleNamespace

import pytest

from . import exporter as exporter_module
from .client import ClientConfigV1Alpha1
from .exporter import ExporterConfigV1Alpha1
from jumpstarter.common.exceptions import ConfigurationError


@pytest.mark.parametrize("alias", ["C:escape", "name:stream", "NUL", "CON.txt", "COM1", "LPT9.yaml"])
def test_windows_alias_cannot_address_drive_stream_or_device(monkeypatch, alias):
    monkeypatch.setattr(exporter_module, "sys", SimpleNamespace(platform="win32"))
    monkeypatch.setattr(ExporterConfigV1Alpha1, "BASE_PATH", PureWindowsPath("D:/User config/exporters"))

    with pytest.raises(ConfigurationError, match="Invalid exporter alias"):
        ExporterConfigV1Alpha1._get_path(alias)


def test_windows_alias_with_unicode_and_spaces_stays_in_user_directory(monkeypatch):
    monkeypatch.setattr(exporter_module, "sys", SimpleNamespace(platform="win32"))
    base = PureWindowsPath("D:/User config/exporters")
    monkeypatch.setattr(ExporterConfigV1Alpha1, "BASE_PATH", base)
    assert ExporterConfigV1Alpha1._get_path("Lab 配置") == base / "Lab 配置.yaml"


@pytest.mark.parametrize("alias", ["bench:one", "CON", "COM1"])
def test_posix_aliases_retain_native_filename_semantics(monkeypatch, alias):
    monkeypatch.setattr(exporter_module, "sys", SimpleNamespace(platform="linux"))
    base = PurePosixPath("/home/user/.config/jumpstarter/exporters")
    monkeypatch.setattr(ExporterConfigV1Alpha1, "BASE_PATH", base)
    assert ExporterConfigV1Alpha1._get_path(alias) == base / f"{alias}.yaml"


def test_config_save_does_not_require_fchmod(monkeypatch, tmp_path):
    # os.fchmod is unavailable on Windows before Python 3.13.
    monkeypatch.delattr(os, "fchmod", raising=False)
    metadata = {"name": "windows", "namespace": "default"}

    client = ClientConfigV1Alpha1(metadata=metadata, endpoint="localhost:1443", token="token")
    ClientConfigV1Alpha1.save(client, tmp_path / "client.yaml")
    assert ClientConfigV1Alpha1.from_file(tmp_path / "client.yaml").metadata.name == "windows"

    exporter = ExporterConfigV1Alpha1(metadata=metadata, endpoint="localhost:1443", token="token")
    ExporterConfigV1Alpha1.save(exporter, str(tmp_path / "exporter.yaml"))
    assert ExporterConfigV1Alpha1.load_path(tmp_path / "exporter.yaml").metadata.name == "windows"
