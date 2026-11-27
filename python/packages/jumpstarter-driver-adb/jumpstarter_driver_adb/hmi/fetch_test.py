"""The image-build helper: downloads the pinned server from a base URL and keeps it only if its hash is the pin."""

from __future__ import annotations

import hashlib
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import pytest

from . import fetch as fetch_module
from . import server_binary
from jumpstarter.common.exceptions import ConfigurationError

DATA = b"PK\x03\x04 a stand-in for the server"


@pytest.fixture
def mirror():
    """A loopback web server laid out like the project's releases; ``mirror.requests`` are the paths asked for."""

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            mirror.requests.append(self.path)
            if self.path != f"/{server_binary.RELEASE_ASSET}":
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Length", str(len(mirror.body)))
            self.end_headers()
            self.wfile.write(mirror.body)

        def log_message(self, format, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    mirror = SimpleNamespace(requests=[], body=DATA, url=f"http://127.0.0.1:{server.server_address[1]}")
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield mirror
    server.shutdown()
    server.server_close()


def test_the_download_of_the_pinned_build_is_kept(tmp_path, mirror):
    dest = tmp_path / "opt" / "scrcpy"
    target = fetch_module.fetch(dest, mirror.url, hashlib.sha256(DATA).hexdigest())
    assert target == dest / "scrcpy-server" and target.read_bytes() == DATA
    assert target.stat().st_mode & 0o777 == 0o644
    assert [p.name for p in dest.iterdir()] == ["scrcpy-server"]  # no temporary file is left
    assert mirror.requests == [f"/{server_binary.RELEASE_ASSET}"]


def test_a_download_that_is_not_the_pinned_build_is_deleted_and_nothing_is_installed(tmp_path, mirror):
    with pytest.raises(ConfigurationError, match="not the pinned scrcpy-server"):
        fetch_module.fetch(tmp_path, mirror.url)  # the pin is the real one, which DATA is not
    assert list(tmp_path.iterdir()) == []


def test_a_file_already_there_is_replaced_only_by_a_verified_one(tmp_path, mirror):
    (tmp_path / "scrcpy-server").write_bytes(b"old")
    with pytest.raises(ConfigurationError):
        fetch_module.fetch(tmp_path, mirror.url)
    assert (tmp_path / "scrcpy-server").read_bytes() == b"old"
    fetch_module.fetch(tmp_path, mirror.url, hashlib.sha256(DATA).hexdigest())
    assert (tmp_path / "scrcpy-server").read_bytes() == DATA


def test_a_missing_download_is_an_error_and_leaves_nothing(tmp_path, mirror):
    with pytest.raises(OSError, match="404"):
        fetch_module.fetch(tmp_path, mirror.url + "/elsewhere", hashlib.sha256(DATA).hexdigest())
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("base", ["file:///etc", "ftp://example.test", "/opt/mirror", ""])
def test_only_http_and_https_mirrors_are_used(tmp_path, base):
    with pytest.raises(ConfigurationError, match="http or https"):
        fetch_module.fetch(tmp_path, base)
    assert list(tmp_path.iterdir()) == []


def test_the_default_source_is_the_public_release_and_a_mirror_keeps_its_layout():
    assert fetch_module.release_url() == server_binary.RELEASE_URL
    assert fetch_module.release_url("https://mirror.example/scrcpy/") == (
        f"https://mirror.example/scrcpy/{server_binary.RELEASE_ASSET}"
    )


def test_the_command_line_names_the_file_it_installed(tmp_path, mirror, monkeypatch, capsys):
    monkeypatch.setattr(fetch_module, "PINNED_SHA256", hashlib.sha256(DATA).hexdigest())
    assert fetch_module.main(["--dest", str(tmp_path), "--base-url", mirror.url]) == 0
    assert str(tmp_path / "scrcpy-server") in capsys.readouterr().out


def test_the_command_line_says_why_it_refused(tmp_path, mirror, capsys):
    assert fetch_module.main(["--dest", str(tmp_path), "--base-url", mirror.url]) == 1
    assert "refused" in capsys.readouterr().err
    assert fetch_module.main(["--dest", str(tmp_path), "--base-url", mirror.url + "/nope"]) == 1
    assert "could not download" in capsys.readouterr().err
    assert list(tmp_path.iterdir()) == []


def test_the_command_line_needs_a_destination(capsys):
    with pytest.raises(SystemExit):
        fetch_module.main([])
