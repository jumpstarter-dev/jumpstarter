import os
import ssl
from urllib.parse import urlparse

import grpc


def ssl_channel_credentials(target: str, insecure=False):
    if insecure or os.getenv("JUMPSTARTER_GRPC_INSECURE") == "1":
        parsed = urlparse(f"//{target}")
        port = parsed.port if parsed.port else 443
        root_certificates = ssl.get_server_certificate((parsed.hostname, port))
        return grpc.ssl_channel_credentials(root_certificates=root_certificates.encode())
    else:
        return grpc.ssl_channel_credentials()
