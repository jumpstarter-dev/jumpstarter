import base64
import logging
import os
import socket
import ssl
from contextlib import contextmanager
from typing import Any, Sequence, Tuple
from urllib.parse import urlparse

import anyio
import grpc
from anyio import connect_tcp, fail_after

from jumpstarter.common.exceptions import ConfigurationError, ConnectionError

logger = logging.getLogger(__name__)


async def _try_connect_and_extract_cert(
    ip_address: str, port: int, ssl_context: ssl.SSLContext, hostname: str
) -> bytes:
    """
    Try to connect to a single IP and extract its certificate chain.

    Returns the certificate chain in PEM format as bytes.
    Raises exception on failure.

    The caller is expected to enforce the overall timeout via an outer
    fail_after scope so that a slow IP does not consume the entire budget.
    """
    logger.debug(f"Attempting TLS connection to {ip_address}:{port}")
    stream = await connect_tcp(ip_address, port, tls=True, ssl_context=ssl_context, tls_hostname=hostname)
    logger.debug(f"Successfully connected to {ip_address}:{port}")
    try:
        ssl_object = stream.extra(anyio.abc.TLSAttribute.ssl_object)
        # CPython internal: _sslobj.get_unverified_chain() is not part of the
        # public ssl module API. There is no public alternative for extracting
        # the full certificate chain including untrusted intermediates. This
        # will break on non-CPython implementations (PyPy, GraalPy).
        cert_chain = ssl_object._sslobj.get_unverified_chain()
        root_certificates = ""
        for cert in cert_chain:
            root_certificates += cert.public_bytes()
        logger.debug(f"Successfully extracted {len(cert_chain)} certificate(s) from {ip_address}:{port}")

        return root_certificates.encode()
    finally:
        await stream.aclose()


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

            addr_info = await anyio.getaddrinfo(
                parsed.hostname, port, family=socket.AF_UNSPEC, type=socket.SOCK_STREAM
            )

            resolved_ips = [sockaddr[0] for _, _, _, _, sockaddr in addr_info]
            logger.debug(
                f"Resolved {parsed.hostname} to {len(resolved_ips)} IP(s): {', '.join(resolved_ips)}"
            )

            send_stream, receive_stream = anyio.create_memory_object_stream[
                tuple[str, bytes | None, Exception | None]
            ](max_buffer_size=len(addr_info))

            async def try_with_ip(ip_address: str):
                try:
                    result = await _try_connect_and_extract_cert(
                        ip_address, port, ssl_context, parsed.hostname
                    )
                    await send_stream.send((ip_address, result, None))
                except Exception as e:
                    await send_stream.send((ip_address, None, e))

            async with anyio.create_task_group() as tg:
                for _family, _type, _proto, _canonname, sockaddr in addr_info:
                    ip_address_str = sockaddr[0]
                    tg.start_soon(try_with_ip, ip_address_str)

                errors = {}
                results_received = 0
                total_tasks = len(addr_info)

                while results_received < total_tasks:
                    ip_addr, root_certificates, error = await receive_stream.receive()
                    results_received += 1

                    if error is None:
                        logger.debug(f"Using certificates from {ip_addr}:{port}")
                        tg.cancel_scope.cancel()
                        return grpc.ssl_channel_credentials(root_certificates=root_certificates)

                    if isinstance(error, ssl.SSLError):
                        logger.error(f"SSL error on {ip_addr}:{port}: {error}")
                    else:
                        logger.warning(
                            f"Failed to connect to {ip_addr}:{port}: {type(error).__name__}: {error}"
                        )
                    errors[ip_addr] = error

                raise ConnectionError(
                    f"Failed connecting to {parsed.hostname}:{port} - all IPs exhausted. Errors: {errors}"
                )
    except socket.gaierror as e:
        raise ConnectionError(f"Failed resolving {parsed.hostname}") from e
    except TimeoutError as e:
        raise ConnectionError(f"Timeout connecting to {parsed.hostname}:{port}") from e


async def ssl_channel_credentials(target: str, tls_config, timeout=5):
    """Get SSL channel credentials for gRPC connection."""
    if tls_config.insecure or os.getenv("JUMPSTARTER_GRPC_INSECURE") == "1" or os.getenv("JMP_GRPC_INSECURE") == "1":
        return await _ssl_channel_credentials_insecure(target, timeout)
    elif tls_config.ca != "":
        ca_certificate = base64.b64decode(tls_config.ca)
        return grpc.ssl_channel_credentials(ca_certificate)
    else:
        return grpc.ssl_channel_credentials()


def aio_secure_channel(
    target: str,
    credentials: grpc.ChannelCredentials,
    grpc_options: dict[str, Any] | None,
    *,
    interceptors: list | None = None,
):
    return grpc.aio.secure_channel(
        target,
        credentials,
        options=_override_default_grpc_options(grpc_options),
        interceptors=interceptors,
    )


def _override_default_grpc_options(grpc_options: dict[str, str | int] | None) -> Sequence[Tuple[str, Any]]:
    defaults = (
        ("grpc.lb_policy_name", "round_robin"),
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
            raise ConnectionError(f"grpc error: {e.details()}") from None
        if e.code().name == "UNKNOWN":
            raise ConnectionError(f"grpc controller responded: {e.details()}") from None
        else:
            raise ConnectionError("grpc error") from e
    except grpc.RpcError as e:
        raise ConnectionError("grpc error") from e
    except ValueError as e:
        raise ConfigurationError("grpc error") from e
    except Exception as e:
        raise e
