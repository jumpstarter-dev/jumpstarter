r"""Scoped terminal output modes backed by the Python-independent Rust core.

The native ``OutputMode`` class is currently available only on Windows. Use it
when standard output is an attached Windows console::

    import sys

    from jumpstarter_core.console import OutputMode

    if sys.stdout.isatty():
        with OutputMode():
            sys.stdout.write("\x1b[32mConsole ready\x1b[0m\n")
            sys.stdout.flush()

Construction does not change the terminal. Entering the scope captures the mode
and enables processed VT output while preserving wrapping and every other flag.
Scope exit restores the captured mode, including when the body raises an
exception. ``close()`` also restores it and is idempotent; dropping an active
guard attempts restoration as a last resort. Failures raise ``OSError``.

Windows backend
---------------
The guard uses the standard-output handle captured on entry and never closes
or replaces it. Redirected files and pipes are rejected. Modes belong to the
shared console screen buffer, so callers must serialize changes and close nested
guards in reverse order. Flush output before exiting the scope. The guard does not read input, write output, or change input
modes or encodings.

Jumpstarter's Windows serial console uses prompt-toolkit for input and writes
decoded text through Python's output stream. Rust consumers use
``jumpstarter_proc::console::OutputMode::stdout()`` with explicit ``restore()``
and best-effort restoration on drop, without building the Python extension.
"""

from ._core import OutputMode

__all__ = ["OutputMode"]
