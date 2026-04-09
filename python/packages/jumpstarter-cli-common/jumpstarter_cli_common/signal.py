import signal

import click
from anyio import open_signal_receiver
from anyio.abc import CancelScope


# Reference: https://github.com/agronholm/anyio/blob/4.9.0/docs/signals.rst
async def signal_handler(scope: CancelScope):
    with open_signal_receiver(signal.SIGINT, signal.SIGTERM) as signals:
        async for signum in signals:
            match signum:
                case signal.SIGINT:
                    click.echo("SIGINT pressed, terminating", err=True)
                case signal.SIGTERM:
                    click.echo("SIGTERM received, terminating", err=True)
                case _:
                    pass

            scope.cancel()

            break
