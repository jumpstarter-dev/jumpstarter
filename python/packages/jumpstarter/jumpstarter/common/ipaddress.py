import logging
import socket
from ipaddress import ip_address


def get_ip_address(logger: logging.Logger | None = None) -> str:
    """Get the IP address of the host machine"""
    # Try to get the IP address using the hostname
    hostname = socket.gethostname()
    address = socket.gethostbyname(hostname)
    # If it returns nothing or a loopbadck address, do it the hard way
    if not address or ip_address(address).is_loopback:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.connect(("192.175.48.1", 53))  # AS112
                return s.getsockname()[0]
        except Exception:
            if logger:
                logger.warning("Could not determine default IP address, falling back to 0.0.0.0")
            return "0.0.0.0"

    return address
