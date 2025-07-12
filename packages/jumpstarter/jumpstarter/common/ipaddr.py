import asyncio
import logging
import socket
from ipaddress import ip_address


def get_ip_address(logger: logging.Logger | None = None) -> str:
    """Get the IP address of the host machine"""
    # Try to get the IP address using the hostname
    hostname = socket.gethostname()
    try:
        address = socket.gethostbyname(hostname)
        # If it returns nothing or a loopback address, do it the hard way
        if not address or ip_address(address).is_loopback:
            raise socket.gaierror("loopback address, trying fallback")
    except socket.gaierror:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.connect(("192.175.48.1", 53))  # AS112
                return s.getsockname()[0]
        except Exception:
            if logger:
                logger.warning("Could not determine default IP address, falling back to 0.0.0.0")
            return "0.0.0.0"

    return address


async def get_minikube_ip(profile: str = None, minikube: str = "minikube"):
    # Create the subprocess with optional profile
    cmd = [minikube, "ip"]
    if profile:
        cmd.extend(["-p", profile])

    process = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)

    # Wait for it to complete and get the output
    stdout, stderr = await process.communicate()

    # Decode and strip whitespace
    result = stdout.decode().strip()

    # Optional: check if command was successful
    if process.returncode != 0:
        raise RuntimeError(stderr.decode())

    return result
