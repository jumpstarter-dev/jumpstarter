"""Forward a `jmp` subcommand to the Rust CLI command tree via FFI (jumpstarter_core.run_cli).

The controller/admin commands live in the Rust core. The Python `jmp` entrypoint stays for
distribution/consistency (`pip install jumpstarter-all` keeps `jmp` working) but delegates
these subcommands to Rust, which does its own argument parsing, output, and exit codes. The
driver-dependent commands (`run` = driver host, `j` = driver clients) stay native Python and
reach the core through the foreign-trait seam.
"""

from __future__ import annotations

import asyncio

import click


def rust_command(path: str | list[str], short_help: str) -> click.Command:
    """A click command that forwards all of its arguments to ``jmp <path…> …`` in the Rust CLI.

    ``path`` is the Rust command path below ``jmp`` — a single name (``"shell"``) for a
    top-level command, or a list (``["admin", "create", "client"]``) for a nested one. The
    leaf segment becomes the click command name. ``--help`` is left unhandled by click
    (``add_help_option=False``) so it passes through to the Rust command, which renders the
    authoritative help.
    """
    segments = [path] if isinstance(path, str) else list(path)
    name = segments[-1]

    @click.command(
        name=name,
        short_help=short_help,
        add_help_option=False,
        context_settings={"ignore_unknown_options": True, "allow_extra_args": True},
    )
    @click.argument("args", nargs=-1, type=click.UNPROCESSED)
    def _forwarded(args: tuple[str, ...]) -> None:
        import jumpstarter_core as jc

        raise SystemExit(asyncio.run(jc.run_cli(["jmp", *segments, *args])))

    return _forwarded
