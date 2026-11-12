from collections.abc import Callable
from inspect import isasyncgenfunction

import grpc
from anyio import Event

_BEHAVIORS = ("unary_unary", "unary_stream", "stream_unary", "stream_stream")


class RpcsInFlight(grpc.aio.ServerInterceptor):
    """Lets a server's owner wait for the RPCs still running after the server stopped.

    grpc.aio's server.stop() returns once the core has no live calls, which can be
    before the RPCs' tasks have finished: a cancelled handler may still be cleaning
    up, and after a handler returns, gRPC still sends its response in that task.
    """

    def __init__(self):
        self._running: set[Event] = set()

    async def intercept_service(self, continuation, handler_call_details):
        handler = await continuation(handler_call_details)
        if handler is None:
            return None
        for behavior in _BEHAVIORS:
            if getattr(handler, behavior) is not None:
                return handler._replace(**{behavior: self._tracked(getattr(handler, behavior))})
        return handler

    def _tracked(self, behavior: Callable) -> Callable:
        if isasyncgenfunction(behavior):

            async def tracked_responses(request, context):
                self._track(context)
                async for response in behavior(request, context):
                    yield response

            return tracked_responses

        async def tracked(request, context):
            self._track(context)
            return await behavior(request, context)

        return tracked

    def _track(self, context) -> None:
        done = Event()
        self._running.add(done)

        def ended(_context) -> None:
            self._running.discard(done)
            done.set()

        context.add_done_callback(ended)

    async def finished(self) -> None:
        for done in list(self._running):
            await done.wait()
