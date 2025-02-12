import hashlib
from dataclasses import dataclass

from jumpstarter_driver_opendal.client import FileServerClient
from opendal import Operator

from . import CHUNK_SIZE


@dataclass(kw_only=True)
class TftpServerClient(FileServerClient):
    """Client for the TFTP server driver"""

    def put_file(self, filename: str, src_stream, checksum: str | None = None):
        """
        Upload a file to the TFTP server.

        Args:
            filename (str): Name to save the file as on the server
            src_stream: Stream/source to read the file data from
            checksum (str, optional): SHA256 checksum for verification

        Returns:
            str: Name of the uploaded file
        """
        if checksum and self.call("check_file_checksum", filename, checksum):
            self.logger.info(f"Skipping upload of identical file: {filename}")
            return filename

        return self.call("put_file", *(filename, src_stream, checksum))

    def _compute_checksum(self, operator: Operator, path: str) -> str:
      hasher = hashlib.sha256()
      with operator.open(path, "rb") as f:
          while chunk := f.read(CHUNK_SIZE):
              hasher.update(chunk)
      return hasher.hexdigest()
