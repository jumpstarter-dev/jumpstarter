from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Thread

from opendal import Operator

from jumpstarter.common.utils import serve
from jumpstarter.drivers.storage.driver import MockStorageMux


def test_drivers_mock_storage_mux_fs():
    with serve(MockStorageMux(name="storage")) as client:
        with TemporaryDirectory() as tempdir:
            fs = Operator("fs", root=tempdir)

            fs.write("test", b"testcontent" * 1000)

            client.write_file(fs, "test")
            client.write_local_file(str(Path(tempdir) / "test"))


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

    with serve(MockStorageMux(name="storage")) as client:
        server = HTTPServer(("127.0.0.1", 8080), StaticHandler)
        server_thread = Thread(target=server.serve_forever)
        server_thread.daemon = True
        server_thread.start()

        fs = Operator("http", endpoint="http://127.0.0.1:8080")
        client.write_file(fs, "test")

        server.shutdown()
