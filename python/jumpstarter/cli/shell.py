import os
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

import anyio
import click

from jumpstarter.config.exporter import ExporterConfigV1Alpha1


async def shell_impl(name):
    try:
        exporter = ExporterConfigV1Alpha1.load(name)
    except FileNotFoundError as e:
        raise click.ClickException(f"exporter config with name {name} not found: {e}") from e

    with TemporaryDirectory() as tempdir:
        socketpath = Path(tempdir) / "socket"
        async with exporter.serve_local(f"unix://{socketpath}"):
            async with await anyio.open_process(
                [os.environ["SHELL"]],
                stdin=sys.stdin,
                stdout=sys.stdout,
                stderr=sys.stderr,
                env=os.environ
                | {
                    "JUMPSTARTER_HOST": f"unix://{socketpath}",
                },
            ) as process:
                await process.wait()


@click.command()
@click.argument("name", type=str, default="default")
def shell(name):
    """Spawns a shell with a transient exporter session"""
    anyio.run(shell_impl, name)
