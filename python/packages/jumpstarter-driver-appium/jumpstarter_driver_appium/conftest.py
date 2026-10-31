import stat
import sys
import textwrap

import pytest

# A stand-in for `appium server`: it records how it was started and echoes requests.
FAKE_APPIUM = textwrap.dedent(
    """
    import json, os, sys
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    args = sys.argv[1:]
    assert args[0] == "server", args
    port = int(args[args.index("--port") + 1])
    with open(args[args.index("--config") + 1]) as config:
        started = {"args": args, "config": json.load(config), "env": dict(os.environ)}
    with open(os.environ["FAKE_APPIUM_RECORD"], "a") as record:
        record.write(json.dumps(started) + "\\n")
    if os.environ.get("FAKE_APPIUM_FAIL"):
        print("fake appium refused to start", flush=True)
        sys.exit(3)

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args):
            pass

        def reply(self, status, document):
            payload = json.dumps(document).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def do_GET(self):
            if self.path == "/status":
                self.reply(200, {"value": {"ready": True}})
            elif self.path == "/big":
                payload = b"x" * (3 * 1024 * 1024)
                self.send_response(200)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
            else:
                self.reply(200, {"value": {"method": "GET", "path": self.path}})

        def do_POST(self):
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length) or b"{}")
            if self.path == "/session":
                self.reply(200, {"value": {"sessionId": "s1", "capabilities": body}})
            else:
                self.reply(200, {"value": {"method": "POST", "path": self.path, "body": body}})

    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
    """
)


@pytest.fixture
def fake_appium(tmp_path, monkeypatch):
    executable = tmp_path / "appium"
    executable.write_text(f"#!{sys.executable}\n{FAKE_APPIUM}")
    executable.chmod(executable.stat().st_mode | stat.S_IXUSR)
    record = tmp_path / "record.jsonl"
    monkeypatch.setenv("FAKE_APPIUM_RECORD", str(record))
    return executable, record
