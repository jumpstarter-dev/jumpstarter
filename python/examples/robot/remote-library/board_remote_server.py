"""A Robot Framework remote library server, standing in for one running on a board.

It speaks Robot Framework's remote library interface (XML-RPC) with the
standard library only, so the example needs no extra package; on a real board
you would usually run PythonRemoteServer (``pip install robotremoteserver``).

    python board_remote_server.py [PORT]
"""

import sys
import traceback
from xmlrpc.server import SimpleXMLRPCServer


class BoardKeywords:
    """Keywords that run on the board itself."""

    def __init__(self):
        self.led = "off"

    def get_temperature(self):
        """Return the board's temperature in degrees Celsius."""
        return 42.5

    def set_led(self, state):
        """Switch the status LED ``on`` or ``off``."""
        if state not in ("on", "off"):
            raise ValueError(f"LED state must be on or off, not {state!r}")
        self.led = state

    def led_should_be(self, state):
        """Fail unless the status LED is ``state``."""
        if self.led != state:
            raise AssertionError(f"LED is {self.led}, expected {state}")


class RemoteLibrary:
    """The remote library interface: https://github.com/robotframework/RemoteInterface"""

    def __init__(self, library):
        self._library = library

    def get_keyword_names(self):
        return [name for name in dir(self._library) if not name.startswith("_")]

    def get_keyword_documentation(self, name):
        return getattr(self._library, name).__doc__ or ""

    def run_keyword(self, name, args, kwargs=None):
        try:
            value = getattr(self._library, name)(*args, **(kwargs or {}))
        except Exception as error:  # noqa: BLE001 - any keyword error is reported to Robot Framework as a failure
            return {"status": "FAIL", "error": str(error), "traceback": traceback.format_exc()}
        return {"status": "PASS", "return": "" if value is None else value}


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8270
    server = SimpleXMLRPCServer(("127.0.0.1", port), logRequests=False, allow_none=True)
    server.register_instance(RemoteLibrary(BoardKeywords()))
    print(f"remote library on 127.0.0.1:{port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
