import hashlib
import os
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from random import randbytes
from tempfile import TemporaryDirectory
from threading import Thread
from unittest import mock

import pytest
from opendal import Operator

from .common import PresignedRequest
from .driver import MockFlasher, MockStorageMux, MockStorageMuxFlasher, Opendal
from jumpstarter.common.utils import serve


@pytest.fixture(scope="function")
def opendal(tmp_path):
    with serve(Opendal(scheme="fs", kwargs={"root": str(tmp_path)})) as client:
        yield client


test_file = "test_file.txt"
test_content = b"hello"


def test_driver_opendal_read_write_bytes(opendal):
    opendal.write_bytes(test_file, test_content)

    assert opendal.read_bytes(test_file) == test_content
    assert opendal.hash(test_file, "md5") == hashlib.md5(test_content).hexdigest()
    assert opendal.hash(test_file, "sha256") == hashlib.sha256(test_content).hexdigest()


def test_driver_opendal_read_write_path(opendal, tmp_path):
    src = tmp_path / "src"
    dst = tmp_path / "dst"

    src.write_bytes(test_content)

    opendal.write_from_path(test_file, src)
    opendal.read_into_path(test_file, dst)

    assert dst.read_bytes() == test_content


def test_driver_opendal_seek_tell(opendal):
    off = -3
    pos = len(test_content) + off

    assert pos >= 0

    opendal.write_bytes(test_file, test_content)

    file = opendal.open(test_file, "rb")
    file.seek(off, os.SEEK_END)

    assert file.tell() == pos
    assert file.read_bytes() == test_content[off:]

    file.close()


def test_driver_opendal_file_property(opendal):
    file = opendal.open(test_file, "wb")

    assert not file.closed
    assert not file.readable()
    assert not file.seekable()
    assert file.writable()

    file.close()

    assert file.closed

    file = opendal.open(test_file, "rb")

    assert not file.closed
    assert file.readable()
    assert file.seekable()
    assert not file.writable()

    file.close()

    assert file.closed


def test_driver_opendal_file_metadata(opendal):
    opendal.write_bytes(test_file, test_content)

    assert opendal.exists(test_file)
    assert opendal.stat(test_file).mode.is_file()

    opendal.copy(test_file, "copy_of_test_file")

    assert opendal.exists("copy_of_test_file")

    opendal.rename("copy_of_test_file", "renamed_copy_of_test_file")

    assert not opendal.exists("copy_of_test_file")
    assert opendal.exists("renamed_copy_of_test_file")

    opendal.delete("renamed_copy_of_test_file")

    assert not opendal.exists("renamed_copy_of_test_file")

    opendal.create_dir("test_dir/")

    assert opendal.exists("test_dir/")

    assert opendal.stat("test_dir/").mode.is_dir()

    opendal.remove_all("test_dir/")

    assert not opendal.exists("test_dir/")


def test_driver_opendal_file_list_scan(opendal):
    opendal.create_dir("a/b/c/")
    opendal.create_dir("d/e/")

    assert sorted(opendal.list("/")) == ["/", "a/", "d/"]
    assert sorted(opendal.scan("/")) == ["/", "a/", "a/b/", "a/b/c/", "d/", "d/e/"]


def test_driver_opendal_presign(tmp_path):
    with serve(Opendal(scheme="http", kwargs={"endpoint": "http://invalid.invalid"})) as client:
        capability = client.capability()

        assert capability.presign_read
        assert client.presign_read("test", 100) == PresignedRequest(
            url="http://invalid.invalid/test", method="GET", headers={}
        )

        assert capability.presign_stat
        assert client.presign_stat("test", 100) == PresignedRequest(
            url="http://invalid.invalid/test", method="HEAD", headers={}
        )


@pytest.mark.parametrize("target", [None, "uboot"])
def test_driver_flasher(tmp_path, target):
    with serve(MockFlasher()) as flasher:
        (tmp_path / "disk.img").write_bytes(b"hello")

        flasher.flash(tmp_path / "disk.img", target=target)
        flasher.dump(tmp_path / "dump.img", target=target)

        assert (tmp_path / "dump.img").read_bytes() == b"hello"


def test_driver_mock_storage_mux_flasher(tmp_path):
    with serve(MockStorageMuxFlasher()) as flasher:
        (tmp_path / "disk.img").write_bytes(b"hello")

        # mock the StorageMuxClient dut/host methods
        with mock.patch.object(flasher, "call", side_effect=flasher.call) as mock_method:
            flasher.flash(tmp_path / "disk.img")
            # assert the mock had a call to "host", "write" and "dut"
            assert mock_method.call_args_list == [
                mock.call("host"),
                mock.call("write", mock.ANY),
                mock.call("dut"),
            ]

            mock_method.reset_mock()
            flasher.dump(tmp_path / "dump.img")
            assert mock_method.call_args_list == [
                mock.call("host"),
                mock.call("read", mock.ANY),
                mock.call("dut"),
            ]

            assert (tmp_path / "dump.img").read_bytes() == b"hello"


def test_drivers_mock_storage_mux_fs(monkeypatch: pytest.MonkeyPatch):
    with serve(MockStorageMux()) as client:
        with TemporaryDirectory() as tempdir:
            # original file on the client to be pushed to the exporter
            original = Path(tempdir) / "original"
            # new file read back from the exporter to the client
            readback = Path(tempdir) / "readback"

            # test accessing files with absolute path

            # fill the original file with random bytes
            original.write_bytes(randbytes(1024 * 1024 * 10))
            # write the file to the storage on the exporter
            client.write_local_file(str(original))
            # read the storage on the exporter to a local file
            client.read_local_file(str(readback))
            # ensure the contents are equal
            assert original.read_bytes() == readback.read_bytes()

            # test accessing files with relative path
            with monkeypatch.context() as m:
                m.chdir(tempdir)

                original.write_bytes(randbytes(1024 * 1024 * 1))
                client.write_local_file("original")
                client.read_local_file("readback")
                assert original.read_bytes() == readback.read_bytes()

                original.write_bytes(randbytes(1024 * 1024 * 1))
                client.write_local_file("./original")
                client.read_local_file("./readback")
                assert original.read_bytes() == readback.read_bytes()


def test_drivers_mock_storage_mux_http():
    # dummy HTTP server returning static test content
    class StaticHandler(BaseHTTPRequestHandler):
        def do_HEAD(self):
            self.send_response(200)
            self.send_header("content-length", 11 * 1000)
            self.end_headers()

        def do_GET(self):
            self.send_response(200)
            self.send_header("content-length", 11 * 1000)
            self.end_headers()
            self.wfile.write(b"testcontent" * 1000)

    with serve(MockStorageMux()) as client:
        # start the HTTP server
        server = HTTPServer(("127.0.0.1", 8080), StaticHandler)
        server_thread = Thread(target=server.serve_forever)
        server_thread.daemon = True
        server_thread.start()

        # write a remote file from the http server to the exporter
        fs = Operator("http", endpoint="http://127.0.0.1:8080")
        client.write_file(fs, "test")

        server.shutdown()
