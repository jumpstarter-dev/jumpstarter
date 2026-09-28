#!/usr/bin/env python3
"""Qualify stock Windows exporter supervision with disposable loopback drivers.

No controller, credentials, hardware or pre-existing exporter is used. The
fixture deliberately restarts its own exporter worker and spawns a sleeping
descendant to verify cleanup. Never load ProcessFixture in a real exporter.
Lifecycle hooks use the default PowerShell executor and a Python executor.
"""

import argparse
import csv
import io
import json
import os
import platform
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

from jumpstarter_driver_power.driver import MockPower
from windows_client_e2e import direct_client, isolated_environment, require, run_bounded

from jumpstarter.driver import export

RESULT_PREFIX = "EXPORTER_FIXTURE_RESULT="
# The PowerShell hook reaches the exporter with j over the Windows hook socket.
BEFORE_LEASE_HOOK = """\
j power on
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
Set-Content -LiteralPath $env:E2E_HOOK_MARKER -Value "before:$env:LEASE_NAME"
"""
AFTER_LEASE_HOOK = """\
import os, pathlib
pathlib.Path(os.environ["E2E_HOOK_MARKER"] + ".after").write_text("after:" + os.environ["LEASE_NAME"])
"""


class ProcessFixture(MockPower):
    """Test-only process descendant and controlled worker-restart trigger."""

    def __post_init__(self):
        super().__post_init__()
        self._descendant = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(600)"],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )

    @export
    def identity(self):
        return {"worker": os.getpid(), "descendant": self._descendant.pid}

    @export
    def restart(self):
        threading.Timer(0.2, lambda: os._exit(0)).start()

    def close(self):
        self._descendant.terminate()
        self._descendant.wait(timeout=5)
        super().close()


def arguments():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report-dir", type=Path, default=Path(".e2e/windows-exporter"))
    parser.add_argument("--timeout", type=float, default=30)
    parser.add_argument("--_worker", choices=("identity", "restart"), help=argparse.SUPPRESS)
    parser.add_argument("--_endpoint", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if sys.platform != "win32":
        parser.error("this runner requires native Windows")
    if not 5 <= args.timeout <= 120:
        parser.error("--timeout must be between 5 and 120 seconds")
    return args


def fixture_worker(args):
    with direct_client(SimpleNamespace(direct_endpoint=args._endpoint, direct_insecure=True)) as client:
        value = client.children["process_probe"].call(args._worker)
    print(RESULT_PREFIX + json.dumps(value), flush=True)
    return 0


def endpoint_open(endpoint):
    try:
        with socket.create_connection(endpoint, timeout=0.2):
            return True
    except OSError:
        return False


def wait_endpoint(process, endpoint, timeout):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        require(process.poll() is None, f"stock jmp run exited before readiness: {process.returncode}")
        if endpoint_open(endpoint):
            return
        time.sleep(0.05)
    raise TimeoutError("stock jmp run did not open its loopback listener")


def process_exists(pid):
    result = subprocess.run(
        ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
        capture_output=True, text=True, check=True, timeout=5,
    )
    return any(len(row) > 1 and row[1] == str(pid) for row in csv.reader(io.StringIO(result.stdout)))


def assert_clean(endpoint, pids, timeout=10):
    deadline = time.monotonic() + timeout
    while True:
        remaining = [pid for pid in pids if process_exists(pid)]
        if not remaining and not endpoint_open(endpoint):
            return
        require(time.monotonic() < deadline, f"exporter resources survived exit: processes={remaining}")
        time.sleep(0.1)


def fixture_call(args, endpoint, method, env):
    result = run_bounded(
        [sys.executable, str(Path(__file__).resolve()), "--_worker", method, "--_endpoint", endpoint],
        env, args.timeout,
    )
    require(result.returncode == 0, f"fixture RPC failed: {result.stderr[-4000:]}")
    lines = [line.removeprefix(RESULT_PREFIX) for line in result.stdout.splitlines() if line.startswith(RESULT_PREFIX)]
    require(len(lines) == 1, f"fixture RPC returned no result: {result.stdout[-2000:]}")
    value = json.loads(lines[0])
    return {name: int(pid) for name, pid in value.items()} if method == "identity" else value


def exercise_clients(args, endpoint, env, directory):
    report = directory / "client-results.json"
    completed = run_bounded([
        sys.executable, str(Path(__file__).with_name("windows_client_e2e.py").resolve()),
        "--direct-endpoint", endpoint, "--direct-insecure", "--timeout", str(args.timeout),
        "--report", str(report),
    ], env, args.timeout * 8 + 20)
    (directory / "client-stdout.log").write_text(completed.stdout, encoding="utf-8")
    (directory / "client-stderr.log").write_text(completed.stderr, encoding="utf-8")
    require(completed.returncode == 0, f"client probe suite failed; see {report}")
    results = json.loads(report.read_text(encoding="utf-8"))["results"]
    direct = [item for item in results if item["probe"].startswith("direct.")]
    require(bool(direct) and all(item["status"] == "PASS" for item in direct), "direct probes incomplete")
    return [item["probe"] for item in direct]


def wait_for_marker(path, expected, timeout):
    deadline = time.monotonic() + timeout
    while True:
        # Windows PowerShell's UTF-8 files start with a BOM.
        if path.exists() and path.read_text(encoding="utf-8-sig").strip() == expected:
            return
        require(time.monotonic() < deadline, f"hook marker {path.name} did not become {expected!r}")
        time.sleep(0.1)


def check_child_logging(directory, workers):
    records = []
    for line in (directory / "exporter-1-stderr.log").read_text(encoding="utf-8").splitlines():
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    logged_pids = {item.get("pid") for item in records
                  if item.get("msg") == "Windows exporter worker started" and item.get("level") == "debug"}
    require(set(workers) <= logged_pids, "spawned workers did not preserve JSON logging and DEBUG level")


def write_config(directory):
    config = {
        "apiVersion": "jumpstarter.dev/v1alpha1", "kind": "ExporterConfig",
        "metadata": {"name": "windows-native-fixture", "namespace": "default"},
        "export": {
            "power": {"type": "jumpstarter_driver_power.driver.MockPower"},
            "process_probe": {"type": "windows_exporter_e2e.ProcessFixture"},
        },
        "hooks": {
            "beforeLease": {"script": BEFORE_LEASE_HOOK, "timeout": 60, "onFailure": "exit"},
            "afterLease": {"exec": "python", "script": AFTER_LEASE_HOOK, "timeout": 60},
        },
    }
    path = directory / "exporter.json"
    path.write_text(json.dumps(config, indent=2), encoding="utf-8")
    return path


def main():
    args = arguments()
    if args._worker:
        return fixture_worker(args)
    args.report_dir.mkdir(parents=True, exist_ok=True)
    directory = Path(tempfile.mkdtemp(prefix="native-", dir=args.report_dir.resolve()))
    env = isolated_environment()
    env["JMP_CLIENT_CONFIG_HOME"] = str(directory / "client-config")
    env["PATH"] = str(Path(sys.executable).parent) + os.pathsep + env.get("PATH", "")
    env["PYTHONPATH"] = str(Path(__file__).resolve().parent) + os.pathsep + env.get("PYTHONPATH", "")
    marker = directory / "hook-marker.txt"
    env["E2E_HOOK_MARKER"] = str(marker)
    results = []
    process = None
    known_pids = set()
    endpoint = None
    logs = []
    try:
        config = write_config(directory)
        with socket.socket() as reservation:
            reservation.bind(("127.0.0.1", 0))
            endpoint = reservation.getsockname()
        endpoint_text = f"{endpoint[0]}:{endpoint[1]}"
        command = [sys.executable, "-m", "jumpstarter_cli", "--log-format", "json", "--log-level", "DEBUG",
                   "run", "--exporter-config", str(config),
                   "--tls-grpc-listener", endpoint_text, "--tls-grpc-insecure"]

        def launch(index):
            stdout = (directory / f"exporter-{index}-stdout.log").open("wb")
            stderr = (directory / f"exporter-{index}-stderr.log").open("wb")
            logs.extend([stdout, stderr])
            return subprocess.Popen(command, env=env, stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr,
                                    creationflags=subprocess.CREATE_NEW_PROCESS_GROUP)

        process = launch(1)
        wait_endpoint(process, endpoint, args.timeout)
        wait_for_marker(marker, "before:standalone", args.timeout)
        results.append({"probe": "before_lease_powershell_hook", "status": "PASS"})
        identity = fixture_call(args, endpoint_text, "identity", env)
        known_pids.update(identity.values())
        results.append({"probe": "stock_launcher_and_clients", "status": "PASS", "identity": identity,
                        "client_probes": exercise_clients(args, endpoint_text, env, directory)})

        fixture_call(args, endpoint_text, "restart", env)
        deadline = time.monotonic() + args.timeout
        while True:
            time.sleep(0.2)
            wait_endpoint(process, endpoint, max(0.1, deadline - time.monotonic()))
            try:
                replacement = fixture_call(args, endpoint_text, "identity", env)
                if replacement["worker"] != identity["worker"]:
                    break
            except (RuntimeError, AssertionError):
                if time.monotonic() >= deadline:
                    raise
            require(time.monotonic() < deadline, "exporter did not restart its worker")
        known_pids.update(replacement.values())
        require(not any(process_exists(pid) for pid in identity.values()), "old worker/descendant survived restart")
        results.append({"probe": "automatic_worker_restart", "status": "PASS", "replacement": replacement})

        process.send_signal(signal.CTRL_BREAK_EVENT)
        process.wait(timeout=args.timeout + 5)
        require(process.returncode == 0, f"unexpected graceful exit {process.returncode}")
        assert_clean(endpoint, known_pids)
        results.append({"probe": "graceful_stop", "status": "PASS", "exit_code": process.returncode})
        wait_for_marker(marker.with_name(marker.name + ".after"), "after:standalone", 1)
        results.append({"probe": "after_lease_python_hook", "status": "PASS"})
        check_child_logging(directory, (identity["worker"], replacement["worker"]))
        results.append({"probe": "child_logging", "status": "PASS"})

        process = launch(2)
        wait_endpoint(process, endpoint, args.timeout)
        replacement = fixture_call(args, endpoint_text, "identity", env)
        known_pids.update(replacement.values())
        results.append({"probe": "restart_same_listener", "status": "PASS", "identity": replacement})
        process.terminate()  # Kill the supervisor only; Job Object must reap its tree.
        process.wait(timeout=5)
        assert_clean(endpoint, known_pids)
        results.append({"probe": "abrupt_supervisor_death", "status": "PASS"})
    except Exception as exc:  # noqa: BLE001 - persist diagnostics and clean only owned processes
        results.append({"probe": "lifecycle", "status": "FAIL", "error": f"{type(exc).__name__}: {exc}"})
    finally:
        if process is not None and process.poll() is None:
            process.terminate()
            process.wait(timeout=5)
        for log in logs:
            log.close()
    report = {
        "recorded_at": datetime.now(UTC).isoformat(), "platform": platform.platform(), "python": sys.version,
        "scope": "stock native Windows jmp run, ephemeral loopback, no controller or hardware",
        "results": results, "report_directory": str(directory),
    }
    output = json.dumps(report, indent=2)
    (directory / "results.json").write_text(output + "\n", encoding="utf-8")
    print(output)
    return int(any(item["status"] != "PASS" for item in results))


if __name__ == "__main__":
    raise SystemExit(main())
