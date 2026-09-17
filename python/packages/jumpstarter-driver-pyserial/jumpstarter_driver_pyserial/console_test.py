import os
import threading
import time
from unittest.mock import MagicMock, patch

from .console import Console
from .driver import PySerial
from jumpstarter.common.utils import serve


def _start_console(client, on_power_cycle=None):
    """Run Console.run() in a thread with a PTY substituted for stdin.

    Returns (master_fd, thread, result_dict). Write keypresses to master_fd;
    the result dict gets an 'exc' key if the console thread raises.
    """
    master_fd, slave_fd = os.openpty()
    slave_file = os.fdopen(slave_fd, "rb", buffering=0)

    mock_stdin = MagicMock()
    mock_stdin.fileno.return_value = slave_fd
    mock_stdin.buffer = slave_file

    result = {}

    def _run():
        with patch("sys.stdin", mock_stdin):
            console = Console(serial_client=client, on_power_cycle=on_power_cycle)
            try:
                console.run()
            except Exception as e:
                result["exc"] = e
        slave_file.close()

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    return master_fd, t, result


def test_ctrl_b_exits():
    with serve(PySerial(url="loop://")) as client:
        master_fd, t, result = _start_console(client)
        try:
            time.sleep(0.1)
            os.write(master_fd, b"a")
            os.write(master_fd, b"\x02\x02\x02")
            t.join(timeout=5)
        finally:
            os.close(master_fd)

    assert not t.is_alive(), "console did not exit after Ctrl-B x3"
    assert "exc" not in result


def test_ctrl_bracket_triggers_power_cycle():
    power_cycled = threading.Event()

    async def on_power_cycle():
        power_cycled.set()

    with serve(PySerial(url="loop://")) as client:
        master_fd, t, result = _start_console(client, on_power_cycle=on_power_cycle)
        try:
            time.sleep(0.1)
            os.write(master_fd, b"\x1d\x1d\x1d")
            assert power_cycled.wait(timeout=5), "power cycle was not triggered"
            assert t.is_alive(), "console exited after power cycle"
            os.write(master_fd, b"\x02\x02\x02")
            t.join(timeout=5)
        finally:
            os.close(master_fd)

    assert not t.is_alive()
    assert "exc" not in result


def test_ctrl_bracket_without_power_client():
    with serve(PySerial(url="loop://")) as client:
        master_fd, t, result = _start_console(client, on_power_cycle=None)
        try:
            time.sleep(0.1)
            os.write(master_fd, b"\x1d\x1d\x1d")
            time.sleep(0.1)
            assert t.is_alive(), "console exited unexpectedly on Ctrl-] without power client"
            os.write(master_fd, b"\x02\x02\x02")
            t.join(timeout=5)
        finally:
            os.close(master_fd)

    assert not t.is_alive()
    assert "exc" not in result
