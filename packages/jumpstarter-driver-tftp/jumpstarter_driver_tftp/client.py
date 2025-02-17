from dataclasses import dataclass

from jumpstarter_driver_opendal.client import FileServerClient


@dataclass(kw_only=True)
class TftpServerClient(FileServerClient):
    """Client for the TFTP server driver"""

    pass
