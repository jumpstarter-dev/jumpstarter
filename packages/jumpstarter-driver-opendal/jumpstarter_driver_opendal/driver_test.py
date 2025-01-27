from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from random import randbytes
from tempfile import TemporaryDirectory
from threading import Thread

import pytest
from opendal import Operator

from .driver import MockStorageMux
from jumpstarter.common.utils import serve


def test_drivers_mock_storage_mux_fs(monkeypatch: pytest.MonkeyPatch):
    with serve(MockStorageMux()) as client:
        with TemporaryDirectory() as tempdir:
            original = Path(tempdir) / "original"
            readback = Path(tempdir) / "readback"

            # absolute path
            original.write_bytes(randbytes(1024 * 1024 * 10))
            client.write_local_file(str(original))
            client.read_local_file(str(readback))
            assert original.read_bytes() == readback.read_bytes()

            # relative path
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
        server = HTTPServer(("127.0.0.1", 8080), StaticHandler)
        server_thread = Thread(target=server.serve_forever)
        server_thread.daemon = True
        server_thread.start()

        fs = Operator("http", endpoint="http://127.0.0.1:8080")
        client.write_file(fs, "test")

        server.shutdown()
