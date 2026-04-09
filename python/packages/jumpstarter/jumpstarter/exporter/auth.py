"""Passphrase-based authentication for standalone gRPC servers and clients."""

import collections
import hmac
import logging

import grpc

logger = logging.getLogger(__name__)

PASSPHRASE_METADATA_KEY = "x-jumpstarter-passphrase"


# ── Server-side ──────────────────────────────────────────────────────


class PassphraseInterceptor(grpc.aio.ServerInterceptor):
    """Reject RPCs that don't carry the correct passphrase in metadata."""

    def __init__(self, passphrase: str):
        self._passphrase = passphrase

    async def intercept_service(self, continuation, handler_call_details):
        metadata = dict(handler_call_details.invocation_metadata or [])
        provided = metadata.get(PASSPHRASE_METADATA_KEY)

        if provided is None or not hmac.compare_digest(provided, self._passphrase):
            # Resolve the real handler to preserve the RPC type, then reject
            handler = await continuation(handler_call_details)
            if handler is None:
                return None
            return _rejection_handler_for(handler)

        return await continuation(handler_call_details)


async def _abort_unauthenticated(request_or_iterator, context):
    await context.abort(grpc.StatusCode.UNAUTHENTICATED, "invalid or missing passphrase")


def _rejection_handler_for(handler):
    """Return a rejection handler matching the RPC type of the original handler."""
    if handler.request_streaming and handler.response_streaming:
        return grpc.stream_stream_rpc_method_handler(_abort_unauthenticated)
    if handler.request_streaming:
        return grpc.stream_unary_rpc_method_handler(_abort_unauthenticated)
    if handler.response_streaming:
        return grpc.unary_stream_rpc_method_handler(_abort_unauthenticated)
    return grpc.unary_unary_rpc_method_handler(_abort_unauthenticated)


# ── Client-side ──────────────────────────────────────────────────────

_ClientCallDetails = collections.namedtuple(
    "_ClientCallDetails",
    ("method", "timeout", "metadata", "credentials", "wait_for_ready"),
)


def _inject_metadata(client_call_details, extra_metadata):
    """Return new call details with extra metadata appended."""
    metadata = list(client_call_details.metadata or [])
    metadata.extend(extra_metadata)
    return _ClientCallDetails(
        client_call_details.method,
        client_call_details.timeout,
        metadata,
        client_call_details.credentials,
        client_call_details.wait_for_ready,
    )


class _UnaryUnaryInterceptor(grpc.aio.UnaryUnaryClientInterceptor):
    def __init__(self, extra_metadata):
        self._extra = extra_metadata

    async def intercept_unary_unary(self, continuation, client_call_details, request):
        return await continuation(_inject_metadata(client_call_details, self._extra), request)


class _UnaryStreamInterceptor(grpc.aio.UnaryStreamClientInterceptor):
    def __init__(self, extra_metadata):
        self._extra = extra_metadata

    async def intercept_unary_stream(self, continuation, client_call_details, request):
        return await continuation(_inject_metadata(client_call_details, self._extra), request)


class _StreamStreamInterceptor(grpc.aio.StreamStreamClientInterceptor):
    def __init__(self, extra_metadata):
        self._extra = extra_metadata

    async def intercept_stream_stream(self, continuation, client_call_details, request_iterator):
        return await continuation(_inject_metadata(client_call_details, self._extra), request_iterator)


def passphrase_client_interceptors(passphrase: str) -> list:
    """Return client interceptors that attach the passphrase to every RPC."""
    extra = [(PASSPHRASE_METADATA_KEY, passphrase)]
    return [
        _UnaryUnaryInterceptor(extra),
        _UnaryStreamInterceptor(extra),
        _StreamStreamInterceptor(extra),
    ]
