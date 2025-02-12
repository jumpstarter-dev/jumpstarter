from pathlib import Path
from urllib.parse import urlparse

import asyncclick as click
from opendal import Operator

from .adapter import OpendalAdapter
from jumpstarter.client import DriverClient


class StorageMuxClient(DriverClient):
    def host(self):
        """Connect storage to host"""
        return self.call("host")

    def dut(self):
        """Connect storage to dut"""
        return self.call("dut")

    def off(self):
        """Disconnect storage"""
        return self.call("off")

    def write(self, handle):
        return self.call("write", handle)

    def read(self, handle):
        return self.call("read", handle)

    def write_file(self, operator: Operator, path: str):
        with OpendalAdapter(client=self, operator=operator, path=path) as handle:
            return self.write(handle)

    def read_file(self, operator: Operator, path: str):
        with OpendalAdapter(client=self, operator=operator, path=path, mode="wb") as handle:
            return self.read(handle)

    def write_local_file(self, filepath):
        """Write a local file to the storage device"""
        absolute = Path(filepath).resolve()
        return self.write_file(operator=Operator("fs", root="/"), path=str(absolute))

    def read_local_file(self, filepath):
        """Read into a local file from the storage device"""
        absolute = Path(filepath).resolve()
        return self.read_file(operator=Operator("fs", root="/"), path=str(absolute))

    def cli(self):
        @click.group
        def base():
            """Generic storage mux"""
            pass

        @base.command()
        def host():
            """Connect storage to host"""
            self.host()

        @base.command()
        def dut():
            """Connect storage to dut"""
            self.dut()

        @base.command()
        def off():
            """Disconnect storage"""
            self.off()

        @base.command()
        @click.argument("file")
        def write_local_file(file):
            self.write_local_file(file)

        return base

class FileServerClient(DriverClient):
    """Base client for file server implementations (HTTP, TFTP, etc)"""

    def start(self):
        """Start the file server"""
        self.call("start")

    def stop(self):
        """Stop the file server"""
        self.call("stop")

    def list_files(self) -> list[str]:
        """List files in the server root directory"""
        return self.call("list_files")

    def put_file(self, filename: str, src_stream, checksum: str | None = None):
        """
        Upload a file to the server using a streamed source.

        Args:
            filename (str): Name to save the file as on the server
            src_stream: Stream/source to read the file data from
            checksum (str, optional): File checksum for verification (if supported)

        Returns:
            str: Result of the upload operation (implementation specific)
        """
        if hasattr(self, "check_file_checksum") and checksum:
            if self.call("check_file_checksum", filename, checksum):
                self.logger.info(f"Skipping upload of identical file: {filename}")
                return filename

        if "client_checksum" in self.call("put_file").__code__.co_varnames:
            return self.call("put_file", filename, src_stream, checksum)

        return self.call("put_file", filename, src_stream)

    def put_file_from_source(self, source: str):
        """
        Upload a file from either a local path or URL to the server.

        Args:
            source (str): Local file path or URL (http/https) to upload

        Returns:
            str: Result of the upload operation (implementation specific)
        """
        if source.startswith(('http://', 'https://')):
            parsed_url = urlparse(source)
            operator = Operator(
                'http',
                root='/',
                endpoint=f"{parsed_url.scheme}://{parsed_url.netloc}"
            )
            filename = parsed_url.path.split('/')[-1]
            path = parsed_url.path
            if path.startswith('/'):
                path = path[1:]
        else:
            operator = Operator('fs', root='/')
            path = str(Path(source).resolve())
            filename = Path(path).name

        with OpendalAdapter(client=self, operator=operator, path=path, mode="rb") as handle:
            return self.put_file(filename, handle)

    def delete_file(self, filename: str) -> str:
        """Delete a file from the server"""
        return self.call("delete_file", filename)

    def get_host(self) -> str:
        """Get the host address the server is listening on"""
        return self.call("get_host")

    def get_port(self) -> int:
        """Get the port number the server is listening on"""
        return self.call("get_port")
