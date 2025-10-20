import asyncio
import base64
import logging
import os
import socket
import ssl
from contextlib import contextmanager
from typing import Any, Sequence, Tuple
from urllib.parse import urlparse

import grpc
from anyio import fail_after

from jumpstarter.common.exceptions import ConfigurationError, ConnectionError

logger = logging.getLogger(__name__)


async def _try_connect_and_extract_cert(
    ip_address: str, port: int, ssl_context: ssl.SSLContext, hostname: str, timeout: float
) -> bytes:
    """
    Try to connect to a single IP and extract its certificate chain.

    Returns the certificate chain in PEM format as bytes.
    Raises exception on failure.
    """
    logger.debug(f"Attempting TLS connection to {ip_address}:{port} (timeout={timeout}s)")
    _, writer = await asyncio.wait_for(
        asyncio.open_connection(ip_address, port, ssl=ssl_context, server_hostname=hostname),
        timeout=timeout,
    )
    logger.debug(f"Successfully connected to {ip_address}:{port}")
    try:
        # Extract certificates
        cert_chain = writer.get_extra_info("ssl_object")._sslobj.get_unverified_chain()
        root_certificates = ""
        for cert in cert_chain:
            root_certificates += cert.public_bytes()
        logger.debug(f"Successfully extracted {len(cert_chain)} certificate(s) from {ip_address}:{port}")

        return root_certificates.encode()
    finally:
        writer.close()


async def _ssl_channel_credentials_insecure(target: str, timeout: float) -> grpc.ChannelCredentials:  # noqa: C901
    """
    Extract TLS certificates from server without verification (insecure mode).

    Tries to connect to all resolved IPs in parallel and returns credentials
    from the first successful connection.
    """
    try:
        parsed = urlparse(f"//{target}")
        port = parsed.port if parsed.port else 443
    except ValueError as e:
        raise ConfigurationError(f"Failed parsing {target}") from e

    try:
        with fail_after(timeout):
            ssl_context = ssl.create_default_context()
            ssl_context.check_hostname = False
            ssl_context.verify_mode = ssl.CERT_NONE

            # Resolve all IP addresses for the hostname
            loop = asyncio.get_running_loop()
            addr_info = await loop.getaddrinfo(
                parsed.hostname, port, family=socket.AF_UNSPEC, type=socket.SOCK_STREAM
            )

            # Log resolved IPs
            resolved_ips = [sockaddr[0] for _, _, _, _, sockaddr in addr_info]
            logger.debug(
                f"Resolved {parsed.hostname} to {len(resolved_ips)} IP(s): {', '.join(resolved_ips)}"
            )

            # Try all IPs in parallel - race for first success
            # Wrap tasks to include IP info with results/exceptions
            async def try_with_ip(ip_address: str):
                """Wrapper that returns (ip, result) on success or (ip, exception) on failure."""
                try:
                    result = await _try_connect_and_extract_cert(
                        ip_address, port, ssl_context, parsed.hostname, timeout
                    )
                    return (ip_address, result, None)
                except Exception as e:
                    return (ip_address, None, e)

            tasks = []
            for _family, _type, _proto, _canonname, sockaddr in addr_info:
                ip_address = sockaddr[0]
                task = asyncio.create_task(try_with_ip(ip_address))
                tasks.append(task)

            # Process tasks as they complete
            errors = {}

            try:
                for future in asyncio.as_completed(tasks):
                    ip_address, root_certificates, error = await future

                    if error is None:
                        # Success! Return immediately (cleanup in finally)
                        logger.debug(f"Using certificates from {ip_address}:{port}")
                        return grpc.ssl_channel_credentials(root_certificates=root_certificates)

                    # This IP failed - log and continue trying other IPs
                    if isinstance(error, ssl.SSLError):
                        logger.error(f"SSL error on {ip_address}:{port}: {error}")
                    else:
                        logger.warning(f"Failed to connect to {ip_address}:{port}: {type(error).__name__}: {error}")
                    errors[ip_address] = error

                # All IPs failed
                raise ConnectionError(
                    f"Failed connecting to {parsed.hostname}:{port} - all IPs exhausted. Errors: {errors}"
                )
            finally:
                # Cancel any remaining tasks
                for task in tasks:
                    if not task.done():
                        task.cancel()
    except socket.gaierror as e:
        raise ConnectionError(f"Failed resolving {parsed.hostname}") from e
    except TimeoutError as e:
        raise ConnectionError(f"Timeout connecting to {parsed.hostname}:{port}") from e


async def ssl_channel_credentials(target: str, tls_config, timeout=5):
    """Get SSL channel credentials for gRPC connection."""
    if tls_config.insecure or os.getenv("JUMPSTARTER_GRPC_INSECURE") == "1":
        return await _ssl_channel_credentials_insecure(target, timeout)
    elif tls_config.ca != "":
        ca_certificate = base64.b64decode(tls_config.ca)
        return grpc.ssl_channel_credentials(ca_certificate)
    else:
        return grpc.ssl_channel_credentials()


def aio_secure_channel(target: str, credentials: grpc.ChannelCredentials, grpc_options: dict[str, Any] | None):
    return grpc.aio.secure_channel(
        target,
        credentials,
        options=_override_default_grpc_options(grpc_options),
    )


def _override_default_grpc_options(grpc_options: dict[str, str | int] | None) -> Sequence[Tuple[str, Any]]:
    defaults = (
        ("grpc.lb_policy_name", "round_robin"),
        # we keep a low keepalive time to avoid idle timeouts on cloud load balancers
        ("grpc.keepalive_time_ms", 20000),
        ("grpc.keepalive_timeout_ms", 180000),
        ("grpc.http2.max_pings_without_data", 0),
        ("grpc.keepalive_permit_without_calls", 1),
    )
    options = dict(defaults)
    options.update(grpc_options or {})
    return tuple(options.items())


@contextmanager
def translate_grpc_exceptions():
    """Translate grpc exceptions to JumpstarterExceptions."""
    try:
        yield
    except grpc.aio.AioRpcError as e:
        if e.code().name == "UNAVAILABLE":
            # tls or other connection errors
            raise ConnectionError(f"grpc error: {e.details()}") from None
        if e.code().name == "UNKNOWN":
            # an error returned from our functions
            raise ConnectionError(f"grpc controller responded: {e.details()}") from None
        else:
            raise ConnectionError("grpc error") from e
    except grpc.RpcError as e:
        raise ConnectionError("grpc error") from e
    except ValueError as e:
        raise ConfigurationError("grpc error") from e
    except Exception as e:
        raise e
