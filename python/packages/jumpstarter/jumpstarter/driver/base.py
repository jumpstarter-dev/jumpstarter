"""
Base classes for drivers and driver clients
"""

from __future__ import annotations

import logging
import os
from abc import ABCMeta, abstractmethod
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from itertools import chain
from typing import Any
from urllib.parse import urlparse, urlunparse
from uuid import UUID

from anyio import BrokenResourceError

from jumpstarter.common import LogSource, Metadata
from jumpstarter.common.resources import ClientStreamResource, PresignedRequestResource, parse_resource
from jumpstarter.config.env import JMP_DISABLE_COMPRESSION
from jumpstarter.exporter.logging import get_logger
from jumpstarter.streams.common import create_memory_stream
from jumpstarter.streams.encoding import Compression, compress_stream
from jumpstarter.streams.progress import ProgressStream

SUPPORTED_CONTENT_ENCODINGS = (
    {}
    if os.environ.get(JMP_DISABLE_COMPRESSION) == "1"
    else {
        Compression.GZIP,
        Compression.XZ,
        Compression.BZ2,
        Compression.ZSTD,
    }
)


@dataclass(kw_only=True)
class Driver(
    Metadata,
    metaclass=ABCMeta,
):
    """Base class for drivers

    Drivers should at the minimum implement the `client` method.

    Regular or streaming driver calls can be marked with the `export` decorator.
    Raw stream constructors can be marked with the `exportstream` decorator.

    The driver tree is introspected (``enumerate``) and dispatched by the in-process Rust
    core via FFI (``jumpstarter.exporter.host``); drivers no longer implement gRPC servicer
    methods. The remaining machinery here is the resource-stream resolver (``resource`` /
    ``_resource_from_client_stream`` / ``_resource_from_presigned``) that the host registers
    into and driver methods read from.
    """

    children: dict[str, Driver] = field(default_factory=dict)

    resources: dict[UUID, Any] = field(default_factory=dict, init=False)
    """Dict of client side resources"""

    description: str | None = None
    """Custom description for the driver (shown in CLI help)"""

    methods_description: dict[str, str] = field(default_factory=dict)
    """Map of method names to their help descriptions (configurable via server config)"""

    log_level: str = "INFO"
    logger: logging.Logger = field(init=False)

    def __post_init__(self):
        if hasattr(super(), "__post_init__"):
            super().__post_init__()

        self.logger = get_logger(f"driver.{self.__class__.__name__}", LogSource.DRIVER)
        self.logger.setLevel(self.log_level)

    def close(self):
        for child in self.children.values():
            child.close()

    def reset(self):
        for child in self.children.values():
            child.reset()

    @classmethod
    @abstractmethod
    def client(cls) -> str:
        """
        Return full import path of the corresponding driver client class
        """

    def extra_labels(self) -> dict[str, str]:
        return {}

    def enumerate(self, *, root=None, parent=None, name=None):
        """
        Get list of self and child devices

        :meta private:
        """
        if root is None:
            root = self

        return [(self.uuid, parent, name, self)] + list(
            chain(*[child.enumerate(root=root, parent=self, name=cname) for (cname, child) in self.children.items()])
        )

    @asynccontextmanager
    async def _resource_from_client_stream(self, resource_uuid: UUID, content_encoding):
        async with self.resources[resource_uuid] as stream:
            try:
                yield compress_stream(stream, content_encoding)
            finally:
                del self.resources[resource_uuid]

    @staticmethod
    def _make_url(url: str):
        """Construct a yarl.URL preserving percent-encoding in the path.

        yarl.URL() normalizes %XX sequences (e.g. %40 → @), which breaks
        signed redirect URLs (CloudFront, S3) whose signatures cover the
        encoded form.  Using encoded=True keeps the raw string intact.
        """
        import yarl

        return yarl.URL(url, encoded=True)

    @staticmethod
    def _redact_url(url: str) -> str:
        """Redact query parameters from a URL to avoid leaking credentials in logs."""
        parsed = urlparse(url)
        if parsed.query:
            return urlunparse(parsed._replace(query="[REDACTED]"))
        return url

    _SENSITIVE_HEADER_PREFIXES = ("authorization", "cookie", "proxy-authorization", "x-amz-", "x-ms-", "x-goog-")

    @classmethod
    def _strip_sensitive_headers(
        cls, headers: dict[str, str], original_url: str, redirect_url: str
    ) -> dict[str, str]:
        """Strip auth headers when a redirect crosses origins."""
        import yarl

        orig = yarl.URL(original_url)
        dest = yarl.URL(redirect_url)
        if (orig.scheme, orig.host, orig.port) == (dest.scheme, dest.host, dest.port):
            return headers
        return {
            k: v for k, v in headers.items()
            if not k.lower().startswith(cls._SENSITIVE_HEADER_PREFIXES)
        }

    @asynccontextmanager
    async def _presigned_get(
        self, url: str, headers: dict[str, str], timeout, max_redirects: int = 10
    ):
        """GET with manual redirect following to preserve percent-encoding in URLs."""
        import aiohttp

        from jumpstarter.streams.aiohttp import AiohttpStreamReaderStream

        current_url = url
        current_headers = headers
        for _ in range(max_redirects + 1):
            async with aiohttp.request(
                "GET", self._make_url(current_url), headers=current_headers, raise_for_status=True,
                timeout=timeout, allow_redirects=False,
            ) as resp:
                if resp.status not in (301, 302, 303, 307, 308):
                    async with AiohttpStreamReaderStream(reader=resp.content) as stream:
                        yield ProgressStream(stream=stream, logging=True)
                        return
                location = resp.headers.get("Location", "")
                if not location:
                    raise RuntimeError(
                        f"Presigned HTTP GET redirect missing Location header for {self._redact_url(current_url)}"
                    )
                current_headers = self._strip_sensitive_headers(current_headers, current_url, location)
                current_url = location
        raise RuntimeError(f"Too many redirects ({max_redirects}) for {self._redact_url(url)}")

    @asynccontextmanager
    async def _resource_from_presigned(self, headers, url: str, method: str, timeout: int):
        import aiohttp

        client_timeout = aiohttp.ClientTimeout(total=timeout)
        try:
            match method:
                case "GET":
                    async with self._presigned_get(url, headers, client_timeout) as stream:
                        yield stream
                case "PUT":
                    remote, stream = create_memory_stream()
                    async with aiohttp.request(
                        method, self._make_url(url), headers=headers, raise_for_status=True,
                        data=remote, timeout=client_timeout,
                    ) as _resp:
                        async with stream:
                            yield ProgressStream(stream=stream, logging=True)
                case _:
                    # INVARIANT: method is always one of GET or PUT, see PresignedRequestResource
                    raise ValueError("unreachable")
        except aiohttp.ClientResponseError as e:
            safe_url = self._redact_url(url)
            raise RuntimeError(
                f"Presigned HTTP {method} request failed: status={e.status}, reason={e.message!r}, url={safe_url}"
            ) from e
        except BrokenResourceError as e:
            safe_url = self._redact_url(url)
            cause = e.__cause__
            if cause is not None:
                raise RuntimeError(
                    f"Presigned HTTP {method} stream interrupted for {safe_url}: {type(cause).__name__}: {cause!s}"
                ) from e
            raise RuntimeError(f"Presigned HTTP {method} stream interrupted for {safe_url}") from e
        except (aiohttp.ClientConnectionError, aiohttp.ClientPayloadError, aiohttp.ServerTimeoutError) as e:
            safe_url = self._redact_url(url)
            raise RuntimeError(
                f"Presigned HTTP {method} stream failed (connection/read error) for {safe_url}: "
                f"{type(e).__name__}: {e!s}"
            ) from e
        except TimeoutError as e:
            safe_url = self._redact_url(url)
            raise TimeoutError(
                f"Presigned HTTP {method} request timed out after {timeout}s for {safe_url}"
            ) from e
        except OSError as e:
            safe_url = self._redact_url(url)
            raise RuntimeError(
                f"Presigned HTTP {method} stream failed with OS error for {safe_url}: {type(e).__name__}: {e!s}"
            ) from e

    @asynccontextmanager
    async def resource(self, handle: str, timeout: int = 7200):
        handle = parse_resource(handle)
        match handle:
            case ClientStreamResource(uuid=uuid, x_jmp_content_encoding=content_encoding):
                async with self._resource_from_client_stream(uuid, content_encoding) as stream:
                    yield stream
            case PresignedRequestResource(headers=headers, url=url, method=method):
                async with self._resource_from_presigned(headers, url, method, timeout) as stream:
                    yield stream
