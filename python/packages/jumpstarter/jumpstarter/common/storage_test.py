import os
import threading
import time

import pytest
from anyio import create_memory_object_stream, move_on_after

from .storage import write_to_storage_device


@pytest.mark.asyncio
async def test_write_to_storage_device_fsyncs_off_the_event_loop(tmp_path, monkeypatch):
    # A blocking fsync holds the event loop for the whole flush. On an
    # exporter that stalls keepalives and status polls, so a client flashing
    # a multi-gigabyte image sees the connection go dead while the card is
    # still being written. Assert the call lands on a worker thread.
    device = tmp_path / "device"
    device.write_bytes(b"\0" * 1024)

    fsync_threads = []
    real_fsync = os.fsync

    def recording_fsync(fd):
        fsync_threads.append(threading.current_thread())
        return real_fsync(fd)

    monkeypatch.setattr(os, "fsync", recording_fsync)

    send, receive = create_memory_object_stream(max_buffer_size=1)
    await send.send(b"payload")
    await send.aclose()

    await write_to_storage_device(device, receive, leeway=0)

    assert fsync_threads, "fsync was never called"
    assert fsync_threads[0] is not threading.current_thread()


@pytest.mark.asyncio
async def test_write_to_storage_device_keeps_the_descriptor_open_for_fsync(tmp_path, monkeypatch):
    # Cancelling mid-flush must not abandon the worker. The enclosing
    # os.fdopen context closes the descriptor on the way out, so an abandoned
    # worker would fsync a closed -- or recycled -- descriptor.
    device = tmp_path / "device"
    device.write_bytes(b"\0" * 1024)

    observed = []

    def slow_fsync(fd):
        time.sleep(0.3)
        try:
            os.fstat(fd)
            observed.append("open")
        except OSError:
            observed.append("closed")

    monkeypatch.setattr(os, "fsync", slow_fsync)

    send, receive = create_memory_object_stream(max_buffer_size=1)
    await send.send(b"payload")
    await send.aclose()

    with move_on_after(0.1):
        await write_to_storage_device(device, receive, leeway=0)

    assert observed == ["open"]
