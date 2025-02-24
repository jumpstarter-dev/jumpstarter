from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from random import randbytes
from tempfile import TemporaryDirectory
from threading import Thread

import pytest
from opendal import Operator

from .driver import MockStorageMux, Opendal
from jumpstarter.common.utils import serve


def test_drivers_opendal(tmp_path):
    with serve(Opendal(scheme="fs", kwargs={"root": str(tmp_path)})) as client:
        client.create_dir("test_dir/")
        assert client.exists("test_dir/")
        client.remove_all("test_dir/")
        assert not client.exists("test_dir/")


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
