"""HTTP/1.1 gateway from a lease stream to the exporter's loopback Appium.

Requests pass through ``AppiumPolicy`` and are forwarded one at a time;
responses stream back unchanged. Connections stay open between requests, so a
WebDriver client's connection pool does not open a lease stream per command.
"""

import json
import logging
from contextlib import suppress

import anyio
import h11
from anyio import BrokenResourceError, ClosedResourceError, EndOfStream

from .policy import AppiumPolicy, PolicyError

logger = logging.getLogger(__name__)

MAX_REQUEST = 64 * 1024 * 1024
CHUNK_SIZE = 65536
# Hop-by-hop headers describe one connection, not the request or response.
_HOP_BY_HOP = frozenset(
    {
        b"connection",
        b"keep-alive",
        b"proxy-authenticate",
        b"proxy-authorization",
        b"proxy-connection",
        b"te",
        b"trailer",
        b"upgrade",
        b"expect",
    }
)
_REQUEST_FRAMING = frozenset({b"content-length", b"transfer-encoding", b"host"})


def _end_to_end(headers, dropped: frozenset[bytes]) -> list[tuple[bytes, bytes]]:
    """Headers without the hop-by-hop ones, including those the message's Connection names."""
    named = {
        token.strip().lower()
        for name, value in headers
        if name == b"connection"
        for token in value.split(b",")
        if token.strip()
    }
    return [(name, value) for name, value in headers if name not in dropped and name not in named]


class _Abort(Exception):
    """The response already started, so the only way to report failure is closing."""


def _error(status: int, code: str, message: str) -> tuple[int, bytes]:
    return status, json.dumps({"value": {"error": code, "message": message, "stacktrace": ""}}).encode()


async def _send(stream, data: bytes | None) -> None:
    if data:
        await stream.send(data)


async def _respond(connection: h11.Connection, stream, status: int, payload: bytes) -> None:
    headers = [
        ("Content-Type", "application/json; charset=utf-8"),
        ("Content-Length", str(len(payload))),
        ("Cache-Control", "no-store"),
    ]
    await _send(stream, connection.send(h11.Response(status_code=status, headers=headers)))
    await _send(stream, connection.send(h11.Data(data=payload)))
    await _send(stream, connection.send(h11.EndOfMessage()))


async def _read_request(connection: h11.Connection, stream):
    request = None
    body = bytearray()
    while True:
        event = connection.next_event()
        if event is h11.NEED_DATA:
            try:
                data = await stream.receive(CHUNK_SIZE)
            except (EndOfStream, BrokenResourceError, ClosedResourceError):
                data = b""
            connection.receive_data(data)
        elif isinstance(event, h11.Request):
            request = event
        elif isinstance(event, h11.Data):
            body.extend(event.data)
            if len(body) > MAX_REQUEST:
                raise PolicyError("Request body exceeds the gateway limit; upload applications with upload_app()")
        elif isinstance(event, h11.EndOfMessage):
            return request, bytes(body)
        elif isinstance(event, h11.ConnectionClosed):
            if request is not None:
                raise PolicyError("Incomplete HTTP request")
            return None
        else:  # h11.PAUSED cannot happen before the request ends.
            raise PolicyError("Unexpected HTTP framing")


async def _forward(connection: h11.Connection, stream, backend: tuple[str, int], request: h11.Request, body: bytes):
    headers = _end_to_end(request.headers, _HOP_BY_HOP | _REQUEST_FRAMING)
    headers += [
        (b"Host", f"{backend[0]}:{backend[1]}".encode()),
        (b"Content-Length", str(len(body)).encode()),
        (b"Connection", b"close"),
    ]
    try:
        upstream = await anyio.connect_tcp(*backend)
    except OSError:
        await _respond(connection, stream, *_error(502, "unknown error", "The lease's Appium server is unavailable"))
        return
    async with upstream:
        client = h11.Connection(h11.CLIENT, max_incomplete_event_size=65536)
        await upstream.send(client.send(h11.Request(method=request.method, target=request.target, headers=headers)))
        if body:
            await upstream.send(client.send(h11.Data(data=body)))
        await upstream.send(client.send(h11.EndOfMessage()))
        started = []
        try:
            await _relay_response(client, upstream, connection, stream, started)
        except (h11.RemoteProtocolError, OSError, BrokenResourceError, ClosedResourceError) as exc:
            logger.warning("Appium response failed: %s", exc)
            if started:
                raise _Abort from exc
            await _respond(connection, stream, *_error(502, "unknown error", "The lease's Appium server failed"))


async def _relay_response(client: h11.Connection, upstream, connection: h11.Connection, stream, started: list):
    """Stream one response back; ``started`` becomes nonempty once its headers are sent."""
    while True:
        event = client.next_event()
        if event is h11.NEED_DATA:
            try:
                data = await upstream.receive(CHUNK_SIZE)
            except (EndOfStream, BrokenResourceError, ClosedResourceError):
                data = b""
            client.receive_data(data)
        elif isinstance(event, h11.InformationalResponse):
            continue
        elif isinstance(event, h11.Response):
            kept = _end_to_end(event.headers, _HOP_BY_HOP)
            await _send(stream, connection.send(h11.Response(status_code=event.status_code, headers=kept)))
            started.append(True)
        elif isinstance(event, h11.Data):
            await _send(stream, connection.send(h11.Data(data=event.data)))
        elif isinstance(event, h11.EndOfMessage):
            await _send(stream, connection.send(h11.EndOfMessage()))
            return
        else:
            raise h11.RemoteProtocolError("Appium closed the connection mid-response")


async def serve_http(stream, *, backend: tuple[str, int], policy: AppiumPolicy) -> None:
    """Serve requests on ``stream`` until either side closes it."""
    connection = h11.Connection(h11.SERVER, max_incomplete_event_size=16384)
    while True:
        try:
            received = await _read_request(connection, stream)
        except (PolicyError, h11.RemoteProtocolError) as exc:
            # h11 lets a server answer a malformed request before closing.
            with suppress(h11.LocalProtocolError, BrokenResourceError, ClosedResourceError):
                await _respond(connection, stream, *_error(400, "invalid argument", str(exc)[:512]))
            return
        if received is None:
            return
        request, body = received
        try:
            body = policy.request(request.method.decode("ascii"), request.target.decode("ascii"), body)
        except PolicyError as exc:
            await _respond(connection, stream, *_error(400, "invalid argument", str(exc)[:512]))
        else:
            try:
                await _forward(connection, stream, backend, request, body)
            except _Abort:
                return
        if connection.our_state is not h11.DONE or connection.their_state is not h11.DONE:
            return
        connection.start_next_cycle()
