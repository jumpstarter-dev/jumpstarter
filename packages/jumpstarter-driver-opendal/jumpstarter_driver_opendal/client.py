import hashlib
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

CHUNK_SIZE = 4 * 1024 * 1024

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

    def compute_checksum(self, filepath: str | Path) -> str:
        """
        Compute SHA256 checksum of a local file

        Args:
            filepath: Path to the file to checksum

        Returns:
            str: Hex digest of SHA256 hash
        """
        hasher = hashlib.sha256()
        with open(filepath, "rb") as f:
            while chunk := f.read(CHUNK_SIZE):
                hasher.update(chunk)
        return hasher.hexdigest()

    def compute_opendal_checksum(self, operator: Operator, path: str) -> str:
        """
        Compute SHA256 checksum of a file from an OpenDAL operator

        Args:
            operator: OpenDAL operator to read from
            path: Path within the operator's storage

        Returns:
            str: Hex digest of SHA256 hash
        """
        hasher = hashlib.sha256()
        with operator.open(path, "rb") as f:
            while chunk := f.read(CHUNK_SIZE):
                hasher.update(chunk)
        return hasher.hexdigest()

    def check_file_checksum(self, filename: str, expected_checksum: str) -> bool:
        """
        Check if a server-side file matches an expected checksum

        Args:
            filename: Name of file to check
            expected_checksum: Expected SHA256 checksum

        Returns:
            bool: True if checksums match, False otherwise
        """
        return self.call("check_file_checksum", filename, expected_checksum)

    def put_file(self, filename: str, src_stream, checksum: str | None = None):
        """
        Upload a file to the server

        Args:
            filename: Name to save the file as
            src_stream: Source stream to read data from
            checksum: Optional SHA256 checksum for verification
        """
        if checksum is not None:
            try:
                return self.call("put_file", filename, src_stream, checksum)
            except (TypeError, ValueError):
                self.logger.debug("Server does not support checksum verification, falling back to basic upload")

        return self.call("put_file", filename, src_stream)

    def put_file_from_source(self, source: str, checksum: str | None = None):
      """
      Upload a file from either a local path or URL to the server.

      Args:
          source (str): Local file path or URL to upload
          checksum (str, optional): SHA256 checksum of the file. If not provided,
              will be computed for local files only.
      """
      self.logger.info(f"Starting upload from source: {source}")

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

          if checksum is None:
              self.logger.warning("No checksum provided for remote file - skipping verification")
      else:
          operator = Operator('fs', root='/')
          path = str(Path(source).resolve())
          filename = Path(path).name

          if checksum is None:
              self.logger.info(f"Computing checksum for local file: {filename}")
              checksum = self.compute_checksum(source)

      if checksum and self.check_file_checksum(filename, checksum):
          self.logger.info(f"Skipping upload of identical file: {filename}")
          return filename

      self.logger.info(f"Opening adapter for {filename}")
      with OpendalAdapter(client=self, operator=operator, path=path, mode="rb") as handle:
          self.logger.info(f"Putting file {filename}")
          result = self.put_file(filename, handle, checksum)
          self.logger.info(f"Completed upload of {filename}")
          return result

    def delete_file(self, filename: str) -> str:
        """Delete a file from the server"""
        return self.call("delete_file", filename)

    def get_host(self) -> str:
        """Get the host address the server is listening on"""
        return self.call("get_host")

    def get_port(self) -> int:
        """Get the port number the server is listening on"""
        return self.call("get_port")
