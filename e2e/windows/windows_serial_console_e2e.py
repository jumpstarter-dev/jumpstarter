"""Run real Windows console checks against a disposable Linux serial loopback.

Requires the installed Jumpstarter CLI, pywinpty and pywin32 in the test environment.
Those terminal inspection libraries are test dependencies, not runtime dependencies.
The exporter must expose a PySerial loop:// driver named ``serial`` and the other
drivers expected by windows_client_e2e.py. All probes use the direct endpoint;
they do not acquire controller leases. The stream_end probe closes that fixture's
serial driver after verifying that only this harness has attached streams.

Example: python windows_serial_console_e2e.py --direct-endpoint 127.0.0.1:19090 --direct-insecure
"""

import argparse
import codecs
import json
import select
import subprocess
import sys
import tempfile
import threading
import time
from contextlib import ExitStack
from importlib.metadata import version
from pathlib import Path
from types import SimpleNamespace

from windows_client_e2e import ALLOWED_CLIENTS, direct_client, isolated_environment, run_bounded

ROOT = Path(__file__).resolve().parents[2]
LONG_LINE = "LONG_BEGIN|" + "".join(f"{number:03d}abcdefgh|" for number in range(40)) + "LONG_END"


def host(args):
    """Run inside ConPTY and check the inherited console after the CLI exits."""
    import win32console

    def modes():
        return {str(handle): win32console.GetStdHandle(handle).GetConsoleMode() for handle in (-10, -11)}

    if args.output_mode is not None:
        win32console.GetStdHandle(-11).SetConsoleMode(args.output_mode)
    result = {"before": modes(), "isatty": [sys.stdin.isatty(), sys.stdout.isatty()]}
    print("HOST_READY", flush=True)
    if args.probe == "smoke":
        result["smoke_line"] = input("SMOKE_LINE_READY>")
        result["returncode"] = 0
    else:
        command = [sys.executable, "-m", "jumpstarter_cli.j", "serial", "console"]
        if args.probe == "observe":
            command.append("--observe")
        result["command"] = command
        result["returncode"] = subprocess.call(command)
    result["after"] = modes()
    output = win32console.GetStdHandle(-11)
    size = output.GetConsoleScreenBufferInfo()["Size"]
    result["screen"] = output.ReadConsoleOutputCharacter(size.X * size.Y, win32console.PyCOORDType(0, 0))
    print("\nCONSOLE_FINISHED=" + str(result["returncode"]), flush=True)
    result["restore_line"] = input("RESTORE_LINE_READY>")
    result["modes_restored"] = result["after"] == result["before"]
    args._host_report.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print("HOST_REPORT_WRITTEN", flush=True)
    return 0 if result["modes_restored"] and result["returncode"] == 0 else 23

def require(value, message):
    if not value:
        raise AssertionError(message)


class Terminal:
    def __init__(self, command, env, report):
        from winpty import Backend, PtyProcess

        self.process = PtyProcess.spawn(command, env=env, cwd=str(ROOT), dimensions=(40, 160), backend=Backend.ConPTY)
        self.output = ""
        self.decoder = codecs.getincrementaldecoder("utf-8")("replace")
        self.report = report

    def pump(self, timeout=0.05):
        ready, _, _ = select.select([self.process.fileobj], [], [], timeout)
        if ready:
            data = self.process.fileobj.recv(65536)
            text = self.decoder.decode(data)
            self.output += text
            if "\x1b[6n" in text:
                self.process.write("\x1b[1;1R")
            return bool(data)
        return True

    def until(self, predicate, description, timeout=20):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.pump()
            if predicate():
                return
            if not self.process.isalive():
                raise AssertionError(f"child exited waiting for {description}: {self.output[-4000:]!r}")
        raise AssertionError(f"timeout waiting for {description}: {self.output[-4000:]!r}")

    def text(self, text):
        self.until(lambda: text in self.output, repr(text))

    def finish(self):
        self.text("RESTORE_LINE_READY>")
        self.process.write("RESTORE_echo_λ\r")
        self.until(lambda: self.report.exists(), "host report")
        self.until(lambda: not self.process.isalive(), "host process exit")
        report = json.loads(self.report.read_text(encoding="utf-8"))
        require(report["modes_restored"], f"console modes differ: {report}")
        require(report["restore_line"] == "RESTORE_echo_λ", f"restored line input mismatch: {report}")
        require(report["returncode"] == 0, f"j failed: {report}")
        require(self.process.exitstatus == 0, f"host failed: {self.process.exitstatus}")
        return report

    def close(self):
        if self.process.isalive() and "RESTORE_LINE_READY>" in self.output and not self.report.exists():
            self.process.write("RESTORE_after_failure\r")
            deadline = time.monotonic() + 3
            while self.process.isalive() and time.monotonic() < deadline:
                self.pump()
        self.report.with_suffix(".terminal.txt").write_text(self.output, encoding="utf-8")
        if self.process.isalive():
            # Only the harness-owned process is terminated after a failure.
            self.process.close(force=True)
        else:
            self.process.fileobj.close()
            self.process._server.close()


class Capture:
    def __init__(self, client, method):
        self.client = client
        self.method = method
        self.buffer = bytearray()
        self.lock = threading.Lock()
        self.stream = None

    async def run(self, *, task_status):
        async with self.client.stream_async(self.method) as stream:
            self.stream = stream
            task_status.started()
            while True:
                data = await stream.receive()
                with self.lock:
                    self.buffer.extend(data)

    def received(self):
        with self.lock:
            return bytes(self.buffer)

    def send(self, data):
        self.client.portal.call(self.stream.send, data)


def child_env(config_home, args):
    env = isolated_environment()
    env.update({"JUMPSTARTER_HOST": args.direct_endpoint or "",
                "JMP_GRPC_INSECURE": "1" if args.direct_insecure else "0",
                "JMP_DRIVERS_ALLOW": ",".join(ALLOWED_CLIENTS), "JMP_CLIENT_CONFIG_HOME": str(config_home),
                "PYTHONIOENCODING": "utf-8", "NO_COLOR": "1"})
    return env


def interactive_keys(term, capture):
    payload = "KEYS_BEGIN|ascii_42|café_λ_中_😀|KEYS_END"
    term.process.write(payload)
    expected = payload.encode("utf-8")
    term.until(lambda: expected in capture.received(), "Unicode remote bytes")
    term.text("café_λ_中_😀")
    arrows = "\x1b[A\x1b[B\x1b[C\x1b[D"
    term.process.write(arrows)
    expected += arrows.encode("ascii")
    term.until(lambda: expected in capture.received(), "arrow remote bytes")
    controls = "\r\x03AFTER_CTRL_C"
    term.process.write(controls)
    expected += controls.encode("ascii")
    term.until(lambda: expected in capture.received(), "Enter and Ctrl+C remote bytes")
    require(term.process.isalive(), "Ctrl+C interrupted raw serial console")
    term.process.write("\x02\x02")
    expected += b"\x02\x02"
    term.until(lambda: expected in capture.received(), "first two Ctrl+B bytes")
    require(term.process.isalive(), "first two Ctrl+B incorrectly exited")
    term.process.write("\x02")
    term.text("CONSOLE_FINISHED=0")
    require(capture.received().endswith(expected), f"unexpected remote bytes: {capture.received()!r}")


def observe_output(term, capture):
    marker = f"OBSERVER_REMOTE_{term.report.parent.name}"
    output = (marker + "\r\n" + LONG_LINE + "\r\nLINE_CR_OLD\rLINE_CR_NEW\r\n").encode()
    capture.send(output)
    term.text(marker)
    term.text("LINE_CR_NEW")
    term.process.write("OBSERVE_MUST_NOT_SEND_789\x1b[A")
    for _ in range(10):
        term.pump(0.05)
    require(b"OBSERVE_MUST_NOT_SEND" not in capture.received(), "observe sent keyboard bytes remotely")
    term.process.write("\x02\x02\x02")
    term.text("CONSOLE_FINISHED=0")
    require(capture.received().endswith(output), f"observer leaked input bytes: {capture.received()!r}")


def console_io(kind, term, serial, capture):
    term.text("exit with CTRL+B x 3 times")
    term.until(lambda: serial.call("console_status")["total_clients"] == 2, "two serial streams")
    # The status confirms the remote stream; polling input enters raw mode just afterwards.
    for _ in range(5):
        term.pump(0.05)
    if kind == "stream_end":
        require(serial.call("console_status")["total_clients"] == 2, "fixture acquired unrelated streams")
        # Ends both harness streams without stopping the exporter or container.
        serial.call("close")
        term.text("CONSOLE_FINISHED=0")
    elif kind == "interactive":
        interactive_keys(term, capture)
    else:
        observe_output(term, capture)
    remaining = 0 if kind == "stream_end" else 1
    term.until(lambda: serial.call("console_status")["total_clients"] == remaining, "console stream cleanup")


def probe(kind, output_dir, args):
    report = output_dir / (kind + ".json")
    command = [sys.executable, str(Path(__file__).resolve()), "--_host-report", str(report), "--probe", kind]
    if args.output_mode is not None:
        command.extend(["--output-mode", str(args.output_mode)])
    with tempfile.TemporaryDirectory(prefix="console-config-", dir=output_dir) as config_home, ExitStack() as stack:
        env = child_env(config_home, args)
        if kind != "smoke":
            client = stack.enter_context(direct_client(SimpleNamespace(direct_endpoint=env["JUMPSTARTER_HOST"],
                                                                        direct_insecure=args.direct_insecure)))
            serial = client.children["serial"]
            status_before = serial.call("console_status")
            require(status_before["total_clients"] == 0, f"fixture is busy; refusing to disturb it: {status_before}")
            capture = Capture(serial, "connect" if kind == "observe" else "observe")
            future, _ = client.portal.start_task(capture.run)
            stack.callback(future.cancel)
        term = Terminal(command, env, report)
        try:
            term.text("HOST_READY")
            if kind == "smoke":
                term.text("SMOKE_LINE_READY>")
                term.process.write("SMOKE_λ_中\r")
            else:
                console_io(kind, term, serial, capture)
            host_result = term.finish()
            if kind == "observe":
                require(LONG_LINE in host_result["screen"], "console truncated the long line instead of wrapping")
                require("LINE_CR_NEW" in host_result["screen"], "carriage return output failed")
                require("LINE_CR_OLD" not in host_result["screen"], "carriage return did not overwrite previous text")
            result = {"probe": kind, "status": "pass", "host": host_result, "python": sys.version,
                      "packages": {name: version(name) for name in ("grpcio", "prompt-toolkit", "pywinpty", "pywin32")}}
            if kind != "smoke":
                result["captured_hex"] = capture.received().hex()
                future.cancel()
                deadline = time.monotonic() + 10
                while time.monotonic() < deadline:
                    final_status = serial.call("console_status")
                    if final_status["total_clients"] == 0:
                        break
                    time.sleep(0.05)
                require(final_status["total_clients"] == 0, f"stream cleanup failed: {final_status}")
                result["final_serial_status"] = final_status
            return result
        finally:
            if kind != "smoke":
                report.with_suffix(".received.bin").write_bytes(capture.received())
            term.close()


def worker(args):
    try:
        result = probe(args.probe, args.report_dir, args)
    except BaseException as exc:
        result = {"probe": args.probe, "status": "fail", "error": repr(exc)}
        raise
    finally:
        (args.report_dir / f"{args.probe}.result.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8",
        )


def run_worker(kind, output_dir, args):
    command = [sys.executable, str(Path(__file__).resolve()), "--_worker", "--probe", kind,
               "--report-dir", str(output_dir)]
    if args.direct_endpoint:
        command.extend(["--direct-endpoint", args.direct_endpoint])
    if args.direct_insecure:
        command.append("--direct-insecure")
    if args.output_mode is not None:
        command.extend(["--output-mode", str(args.output_mode)])
    result = {"probe": kind, "status": "fail"}
    try:
        completed = run_bounded(command, isolated_environment(), args.timeout)
    except subprocess.TimeoutExpired as exc:
        output = (exc.output or "") + (exc.stderr or "")
        result["error"] = f"probe exceeded {args.timeout}s; terminated its process tree"
    else:
        output = completed.stdout + completed.stderr
        saved = output_dir / f"{kind}.result.json"
        if saved.exists():
            result = json.loads(saved.read_text(encoding="utf-8"))
        if completed.returncode != 0:
            result.update(status="fail", returncode=completed.returncode)
    (output_dir / f"{kind}.worker.log").write_text(output, encoding="utf-8")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--direct-endpoint", help="Host:port of the disposable exporter")
    parser.add_argument("--direct-insecure", action="store_true", help="Use plaintext gRPC for the fixture")
    parser.add_argument("--report-dir", type=Path, help="Parent directory for a fresh report directory")
    parser.add_argument("--probe", choices=["smoke", "interactive", "observe", "stream_end"])
    parser.add_argument("--timeout", type=float, default=60, help="Per-probe process deadline in seconds")
    parser.add_argument("--output-mode", type=int, help="Initial Windows output mode, e.g. 3 without VT enabled")
    parser.add_argument("--_host-report", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--_worker", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args._host_report:
        return host(args)
    if args._worker:
        return worker(args)
    if sys.platform != "win32":
        parser.error("these checks require Windows ConPTY")
    if args.probe != "smoke" and not args.direct_endpoint:
        parser.error("--direct-endpoint must identify a disposable fixture")
    if not 5 <= args.timeout <= 300:
        parser.error("--timeout must be between 5 and 300 seconds")
    output_dir = Path(tempfile.mkdtemp(prefix="console-conpty-", dir=args.report_dir))
    results = []
    try:
        for kind in ([args.probe] if args.probe else ["interactive", "observe", "stream_end"]):
            result = run_worker(kind, output_dir, args)
            results.append(result)
            print(json.dumps({key: result[key] for key in ("probe", "status", "error") if key in result}), flush=True)
            if result["status"] != "pass":
                break
    except BaseException as exc:
        results.append({"status": "fail", "error": repr(exc)})
        raise
    finally:
        (output_dir / "results.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
        print("REPORT=" + str(output_dir), flush=True)
    return 0 if all(item["status"] == "pass" for item in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
