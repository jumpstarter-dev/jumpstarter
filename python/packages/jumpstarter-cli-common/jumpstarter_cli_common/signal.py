import asyncio
import signal
import sys

import click
from anyio import open_signal_receiver
from anyio.abc import CancelScope


async def _wait_for_windows_signal():
    # Windows event loops do not implement add_signal_handler. Standard Python
    # signal callbacks run on the main thread; schedule delivery on the CLI loop.
    loop = asyncio.get_running_loop()
    received = loop.create_future()
    previous_handlers = {}

    def deliver(signum):
        if not received.done():
            received.set_result(signum)

    def handler(signum, frame):
        loop.call_soon_threadsafe(deliver, signum)

    try:
        for signum in (signal.SIGINT, signal.SIGTERM):
            previous_handlers[signum] = signal.signal(signum, handler)
        return await received
    finally:
        for signum, previous in previous_handlers.items():
            signal.signal(signum, previous)


# Reference: https://github.com/agronholm/anyio/blob/4.9.0/docs/signals.rst
async def signal_handler(scope: CancelScope):
    if sys.platform == "win32":
        signum = await _wait_for_windows_signal()
    else:
        with open_signal_receiver(signal.SIGINT, signal.SIGTERM) as signals:
            signum = await anext(signals)

    match signum:
        case signal.SIGINT:
            click.echo("SIGINT pressed, terminating", err=True)
        case signal.SIGTERM:
            click.echo("SIGTERM received, terminating", err=True)
        case _:
            pass

    scope.cancel()
