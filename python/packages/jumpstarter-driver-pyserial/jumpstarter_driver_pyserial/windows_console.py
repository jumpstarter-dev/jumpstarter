"""Windows terminal adapter for the existing remote serial byte stream."""

import codecs
import sys
from contextlib import ExitStack, contextmanager

import click
from anyio import EndOfStream, Event, move_on_after
from anyio.lowlevel import checkpoint
from anyio.streams.file import FileWriteStream


class WindowsTerminal:
    def __enter__(self):
        if sys.stdin is None or not sys.stdin.isatty():
            raise click.ClickException(
                "Interactive serial console requires a terminal. Use 'pipe' for redirected input."
            )

        # Keep this module importable during POSIX doctest collection without
        # installing the Windows-only terminal dependency.
        from prompt_toolkit.input import create_input
        from prompt_toolkit.keys import Keys

        self._stack = ExitStack()
        try:
            self._input = create_input(stdin=sys.stdin)
            self._stack.callback(self._input.close)
            # Legacy Windows input guesses paste events from ordinary key batches.
            # Disable that heuristic so only actual VT bracketed paste is wrapped.
            reader = getattr(self._input, "console_input_reader", None)
            if reader is not None and hasattr(reader, "recognize_paste"):
                reader.recognize_paste = False
            self._stack.enter_context(self._input.raw_mode())
            self._pending = []
            self._input_error = None
            self._attached = False
            self._ignored_keys = (Keys.WindowsMouseEvent, Keys.Ignore)
            self._paste_key = Keys.BracketedPaste
            # Console input consists of UTF-16 code units, potentially split across reads.
            self._input_decoder = codecs.getincrementaldecoder("utf-16-le")("replace")
            self._output_decoder = codecs.getincrementaldecoder("utf-8")("replace")
            self._stdout = sys.stdout
            self._text_output = self._stdout.isatty()
            if self._text_output:
                from jumpstarter_core.console import OutputMode

                self._stack.enter_context(OutputMode())
            self._binary_output = FileWriteStream(self._stdout.buffer)
            return self
        except BaseException:
            self._stack.close()
            raise

    def __exit__(self, *exc):
        try:
            if self._text_output:
                tail = self._output_decoder.decode(b"", final=True).encode("utf-8")
                if tail:
                    self._stdout.buffer.write(tail)
                    self._stdout.flush()
        finally:
            self._stack.close()

    @contextmanager
    def attach(self):
        # prompt-toolkit owns the cancellable Windows console wait and its handles.
        # Attach inside the portal's event loop, not the calling CLI thread.
        self._ready = Event()
        with self._input.attach(self._keys_ready):
            self._attached = True
            try:
                yield
            finally:
                self._attached = False

    def _keys_ready(self):
        # A readiness callback already queued by the library may outlive detach.
        if not self._attached:
            return
        try:
            self._pending.extend(self._input.read_keys())
        except Exception as exc:  # noqa: BLE001 - re-raised by receive() in the serial task
            # Event-loop callbacks cannot raise into the serial task group.
            self._input_error = exc
        finally:
            self._ready.set()

    async def receive(self):
        # The serial console's exit-key filter accepts complete key batches.
        while True:
            await checkpoint()
            if self._input_error is not None:
                raise self._input_error
            keys, self._pending = self._pending, []
            text = "".join(
                (f"\x1b[200~{key.data}\x1b[201~" if key.key == self._paste_key else key.data)
                for key in keys
                if key.key not in self._ignored_keys
            )
            data = self._input_decoder.decode(text.encode("utf-16-le", "surrogatepass")).encode("utf-8")
            if data:
                return data
            if self._input.closed:
                raise EndOfStream
            self._ready = Event()
            # Escape is ambiguous until a following key arrives or the timeout expires.
            with move_on_after(0.1) as timeout:
                await self._ready.wait()
            if timeout.cancel_called:
                self._pending.extend(self._input.flush_keys())

    async def send(self, data):
        if self._text_output:
            # Keep incomplete UTF-8 characters between network reads. Rust scopes
            # the console mode without changing wrapping or interpreting ANSI bytes.
            data = self._output_decoder.decode(data).encode("utf-8")
        if data:
            await self._binary_output.send(data)
