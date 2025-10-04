"""CLI callback adapter for jumpstarter-kubernetes library.

This module provides a callback implementation that adapts the library's
callback interface to CLI-specific behavior using click.
"""

import click


class ClickCallback:
    """Callback that uses click for output and user interaction."""

    def __init__(self, silent: bool = False):
        """Initialize callback.

        Args:
            silent: If True, suppress all output (useful for --output=name mode)
        """
        self.silent = silent

    def progress(self, message: str) -> None:
        """Display a progress or informational message."""
        if not self.silent:
            click.echo(message)

    def success(self, message: str) -> None:
        """Display a success message."""
        if not self.silent:
            click.echo(message)

    def warning(self, message: str) -> None:
        """Display a warning message."""
        if not self.silent:
            click.echo(message)

    def error(self, message: str) -> None:
        """Display an error message."""
        if not self.silent:
            click.echo(message, err=True)

    def confirm(self, prompt: str) -> bool:
        """Ask user for confirmation."""
        if self.silent:
            # In silent mode, we can't ask for confirmation
            # This should only happen if the function is called with force=True
            return True
        return click.confirm(prompt)


class ForceClickCallback(ClickCallback):
    """Callback for force mode operations that skips confirmations."""

    def __init__(self, silent: bool = False):
        """Initialize force callback."""
        super().__init__(silent)

    def confirm(self, prompt: str) -> bool:
        """Always returns True (force mode skips confirmations)."""
        return True
