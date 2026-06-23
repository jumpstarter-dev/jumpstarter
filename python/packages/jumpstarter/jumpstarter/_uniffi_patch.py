"""Bulk-copy patch for the UniFFI-generated Python binding's RustBuffer writer.

UniFFI's generated ``_UniffiRustBufferBuilder.write`` lowers a ``bytes`` payload into the
RustBuffer **one byte at a time** — a Python ``for`` loop with a ctypes element assignment per
byte (see ``jumpstarter_core.jumpstarter_core``)::

    def write(self, value):
        with self._reserve(len(value)):
            for i, byte in enumerate(value):
                self.rbuf.data[self.rbuf.len + i] = byte

That is ~0.1 µs/byte, so every resource/flash transfer (the client lowers each chunk through
this) is capped at ~9.5 MiB/s **regardless of chunk size** — the dominant cost is this per-byte
loop, not the wire, the codec, or h2. Replacing it with a single ``ctypes.memmove`` makes lowering
O(1) Python calls and lifts flash throughput ~8.5x (9.4 → 80+ MiB/s, past the pure-Python
exporter). The read side already slices in bulk, so only ``write`` needs patching.

This is an upstream UniFFI bug; the patch is a contained, idempotent monkey-patch applied when the
``jumpstarter``/``jumpstarter_core`` stack is imported (client *and* exporter-host processes).
"""

import ctypes


def apply() -> None:
    """Bulk-copy both directions of the UniFFI RustBuffer byte path (idempotent, best-effort).

    Two symmetric per-byte bugs:
      * lowering: ``_UniffiRustBufferBuilder.write`` loops byte-by-byte (the client send path);
      * lifting:  ``_UniffiRustBufferStream.read`` slices a ``POINTER(c_char)``, which ctypes
        reads element-by-element (the exporter-host *receive* path — each ``stream_write``).
    Replace each with a single bulk copy (``memmove`` / ``string_at``). Together they take a
    512 MiB flash from ~9 to ~150+ MiB/s.
    """
    try:
        from jumpstarter_core import jumpstarter_core as _binding
    except Exception:
        return  # extension not importable (e.g. pure-driver install) — nothing to patch.

    builder = getattr(_binding, "_UniffiRustBufferBuilder", None)
    if builder is not None and not getattr(getattr(builder, "write", None), "_jmp_bulk", False):

        def write(self, value):
            n = len(value)
            with self._reserve(n):
                # `rbuf.data` is a POINTER(c_char); write `value` at the current offset in one copy.
                ctypes.memmove(
                    ctypes.cast(self.rbuf.data, ctypes.c_void_p).value + self.rbuf.len, value, n
                )

        write._jmp_bulk = True
        builder.write = write

    stream = getattr(_binding, "_UniffiRustBufferStream", None)
    if stream is not None and not getattr(getattr(stream, "read", None), "_jmp_bulk", False):

        def read(self, size):
            if self.offset + size > self.len:
                from jumpstarter_core.jumpstarter_core import InternalError

                raise InternalError("read past end of rust buffer")
            # `data` is a POINTER(c_char); read `size` bytes from the current offset in one copy.
            result = ctypes.string_at(
                ctypes.cast(self.data, ctypes.c_void_p).value + self.offset, size
            )
            self.offset += size
            return result

        read._jmp_bulk = True
        stream.read = read


apply()
