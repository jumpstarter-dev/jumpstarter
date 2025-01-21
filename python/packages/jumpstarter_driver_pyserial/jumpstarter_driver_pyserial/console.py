
import os
import sys
import termios
import threading
import tty

from jumpstarter.client import DriverClient


class Console:
    def __init__(self, serial_client:DriverClient):
        self.serial_client = serial_client

    def run(self):
        with self.serial_client.stream() as stream:
            self._run(stream)

    def _run(self, stream):
        self._stream = stream
        self._old_settings = termios.tcgetattr(0)
        try:
            tty.setraw(sys.stdin.fileno())
            thread_serial_to_stdout = threading.Thread(target=self._copy_serial_to_stdout, daemon=True)
            thread_serial_to_stdout.start()
            ctrl_b_count = 0
            while True:
                data = sys.stdin.buffer.read(1)
                if not data:
                    continue
                if data == b"\x02": # Ctrl-B
                    ctrl_b_count += 1
                    if ctrl_b_count == 3:
                        return
                else:
                    ctrl_b_count = 0
                stream.send(data)
        finally:
            self._reset_terminal()

    def _reset_terminal(self):
        termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, self._old_settings)
        # Clear screen and move cursor to top-left (like \033c\033[2J\033[H).
        print("\033c\033[2J\033[H", end="")

    def _copy_serial_to_stdout(self):
        try:
            while True:
                data = self._stream.receive()
                os.write(sys.stdout.fileno(), data)
                sys.stdout.flush()
        finally:
            self._reset_terminal()

