"""Run a Linux exporter beside a TCP echo target for Windows client E2E tests.

The command passed to this entrypoint is the normal ``jmp run`` invocation.
Override its arguments to run a controller-managed exporter with another config.
The echo target is only reachable inside the container; clients use TcpNetwork.
"""

import signal
import socketserver
import subprocess
import sys
from threading import Thread


class EchoHandler(socketserver.BaseRequestHandler):
    def handle(self):
        while data := self.request.recv(65536):
            self.request.sendall(data)


class EchoServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def main() -> int:
    if len(sys.argv) < 2:
        raise SystemExit("Pass the exporter command, for example: jmp run --exporter-config /fixture/exporter.yaml")

    # Bind before launching the exporter, so its network target is ready as soon
    # as the exporter starts serving RPCs. Connections get independent threads.
    with EchoServer(("127.0.0.1", 19091), EchoHandler) as server:
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        print("Linux TCP echo fixture listening on 127.0.0.1:19091", flush=True)
        process = subprocess.Popen(sys.argv[1:])

        def forward_signal(signum, _frame):
            if process.poll() is None:
                process.send_signal(signum)

        previous_handlers = {
            signum: signal.signal(signum, forward_signal) for signum in (signal.SIGINT, signal.SIGTERM)
        }
        try:
            returncode = process.wait()
            return 128 - returncode if returncode < 0 else returncode
        finally:
            for signum, handler in previous_handlers.items():
                signal.signal(signum, handler)
            server.shutdown()
            thread.join()


if __name__ == "__main__":
    raise SystemExit(main())
