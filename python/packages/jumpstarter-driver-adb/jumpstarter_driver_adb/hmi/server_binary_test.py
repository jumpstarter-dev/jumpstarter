"""The pinned server file: how it is found, and that nothing but the pinned build is pushed."""

from __future__ import annotations

import hashlib

import pytest

from . import server_binary
from jumpstarter.common.exceptions import ConfigurationError


def test_the_pin_is_a_sha256_and_the_url_names_the_version():
    assert len(server_binary.PINNED_SHA256) == 64
    assert f"v{server_binary.PINNED_VERSION}" in server_binary.RELEASE_URL
    assert server_binary.RELEASE_URL == f"{server_binary.RELEASE_BASE_URL}/{server_binary.RELEASE_ASSET}"
    assert server_binary.RELEASE_URL.startswith("https://github.com/Genymobile/scrcpy/releases/download/")
    assert server_binary.device_path(server_binary.PINNED_SHA256).startswith("/data/local/tmp/")


def test_a_session_has_a_file_of_its_own_on_the_device():
    sha = server_binary.PINNED_SHA256
    assert server_binary.device_path(sha, "ab12") != server_binary.device_path(sha)
    assert server_binary.device_path(sha, "ab12").endswith("-ab12.jar")


def test_a_file_with_another_hash_is_refused(tmp_path):
    other = tmp_path / "scrcpy-server"
    other.write_bytes(b"not the server")
    with pytest.raises(ConfigurationError, match="not the pinned scrcpy-server"):
        server_binary.verify(other)
    assert (
        server_binary.verify(other, hashlib.sha256(b"not the server").hexdigest())
        == hashlib.sha256(b"not the server").hexdigest()
    )


def test_locate_says_how_to_get_the_file(tmp_path, monkeypatch):
    monkeypatch.delenv(server_binary.ENV_SERVER, raising=False)
    monkeypatch.setattr(server_binary, "SEARCH_PATHS", (str(tmp_path / "nope"),))
    with pytest.raises(ConfigurationError, match="hmi.fetch"):
        server_binary.locate(None)
    real = tmp_path / "scrcpy-server"
    real.write_bytes(b"x")
    assert server_binary.locate(str(real)) == real
    monkeypatch.setenv(server_binary.ENV_SERVER, str(real))
    assert server_binary.locate(None) == real


def test_a_configured_path_that_is_missing_is_not_replaced_by_another(tmp_path, monkeypatch):
    elsewhere = tmp_path / "elsewhere"
    elsewhere.write_bytes(b"x")
    monkeypatch.setenv(server_binary.ENV_SERVER, str(elsewhere))
    with pytest.raises(ConfigurationError, match="not found"):
        server_binary.locate(str(tmp_path / "missing"))


def test_read_verified_returns_the_bytes_it_checked(tmp_path):
    data = b"the server"
    path = tmp_path / "s"
    path.write_bytes(data)
    assert server_binary.read_verified(path, hashlib.sha256(data).hexdigest()) == data
    with pytest.raises(ConfigurationError, match="Refusing to push"):
        server_binary.read_verified(path, "0" * 64)


def test_the_hash_is_compared_without_regard_to_case(tmp_path):
    path = tmp_path / "s"
    path.write_bytes(b"x")
    assert server_binary.verify(path, hashlib.sha256(b"x").hexdigest().upper())
