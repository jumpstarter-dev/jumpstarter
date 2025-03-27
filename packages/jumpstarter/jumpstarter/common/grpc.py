import asyncio
import base64
import os
import socket
import ssl
from contextlib import contextmanager
from typing import Any, Sequence, Tuple
from urllib.parse import urlparse

import grpc
from anyio import fail_after

from jumpstarter.common.exceptions import ConfigurationError, ConnectionError


async def ssl_channel_credentials(target: str, tls_config, timeout=5):
    configure_grpc_env()
    if tls_config.insecure or os.getenv("JUMPSTARTER_GRPC_INSECURE") == "1":
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
                _, writer = await asyncio.open_connection(parsed.hostname, port, ssl=ssl_context)
                root_certificates = ""
                for cert in writer.get_extra_info("ssl_object")._sslobj.get_unverified_chain():
                    root_certificates += cert.public_bytes()
            return grpc.ssl_channel_credentials(root_certificates=root_certificates.encode())
        except socket.gaierror as e:
            raise ConnectionError(f"Failed resolving {parsed.hostname}") from e
        except ConnectionRefusedError as e:
            raise ConnectionError(f"Failed connecting to {parsed.hostname}:{port}") from e
        except TimeoutError as e:
            raise ConnectionError(f"Timeout connecting to {parsed.hostname}:{port}") from e

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
        ("grpc.keepalive_timeout_ms", 5000),
        ("grpc.http2.max_pings_without_data", 0),
        ("grpc.keepalive_permit_without_calls", 1),
    )
    options = dict(defaults)
    options.update(grpc_options or {})
    return tuple(options.items())


def configure_grpc_env():
    # disable informative logs by default, i.e.:
    # WARNING: All log messages before absl::InitializeLog() is called are written to STDERR
    # I0000 00:00:1739970744.889307   61962 ssl_transport_security.cc:1665] Handshake failed ...
    if os.environ.get("GRPC_VERBOSITY") is None:
        os.environ["GRPC_VERBOSITY"] = "ERROR"
    if os.environ.get("GLOG_minloglevel") is None:
        os.environ["GLOG_minloglevel"] = "2"


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
