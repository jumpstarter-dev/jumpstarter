import logging
import socket


def get_ip_address() -> str:
    """Get the IP address of the host machine"""
    # Try to get the IP address using the hostname
    hostname = socket.gethostname()
    address = socket.gethostbyname(hostname)
    # If it returns a bogus address, do it the hard way
    if not address or address.startswith("127."):
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("1.1.1.1", 0))
        address = s.getsockname()[0]
    return address


def get_default_ip(logger: logging.Logger | None = None):
    """Get the IP address of the default route interface"""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
    except Exception:
        if logger:
            logger.warning("Could not determine default IP address, falling back to 0.0.0.0")
        return "0.0.0.0"
