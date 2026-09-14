"""Pod liveness check using driver intent rather than requiring an always-on guest."""

import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from .cvdcli import cvd_argv, fleet_to_cvds, parse_endpoint, stderr_tail

PROBE_TIMEOUT = 8


def initialize(state_path: str, runtime_id_path: str, endpoint: str) -> None:
    runtime_id = Path(runtime_id_path).read_text().strip()
    if not runtime_id:
        raise RuntimeError("Cuttlefish runtime ID is empty")
    state = {"runtime_id_path": runtime_id_path, "runtime_id": runtime_id, "ports": [], "state": "off"}
    state.update(parse_endpoint(endpoint))
    Path(state_path).write_text(json.dumps(state))


def exec_inventory(state: dict) -> list[dict]:
    """List CVDs through the cvd CLI; failure means the launcher or cvd is down."""
    argv = cvd_argv(state["socket"], state.get("cvd_user", ""), ["fleet"])
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=PROBE_TIMEOUT)
    except OSError as exc:
        raise RuntimeError(f"cannot run jumpstarter-exec: {exc}") from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("cvd fleet timed out") from exc
    if proc.returncode != 0:
        raise RuntimeError(f"cvd fleet failed ({proc.returncode}): {stderr_tail(proc.stderr)}")
    return fleet_to_cvds(proc.stdout)


def http_reachable(state: dict) -> None:
    with urllib.request.urlopen(f"{state['url']}/_debug/statusz", timeout=2):
        pass


def http_inventory(state: dict) -> list[dict]:
    with urllib.request.urlopen(f"{state['url']}/cvds", timeout=2) as response:
        return json.load(response)["cvds"]


def check(state_path: str) -> None:
    state = json.loads(Path(state_path).read_text())
    runtime_id = Path(state["runtime_id_path"]).read_text().strip()
    if not runtime_id or runtime_id != state["runtime_id"]:
        raise RuntimeError("Cuttlefish runtime restarted; release the lease to replace this Pod")
    # Reachability is checked in every state: a dead launcher or Host Orchestrator
    # is a runtime failure even while the guest is intentionally off.
    if state.get("backend") == "exec":
        cvds = exec_inventory(state)
    else:
        http_reachable(state)
        cvds = None
    if state["state"] == "off":
        return
    if state["state"] == "transition" and time.monotonic() < state["deadline"]:
        return
    if state["state"] != "running":
        raise RuntimeError("Cuttlefish operation failed or timed out")
    if cvds is None:
        cvds = http_inventory(state)
    if len(cvds) != 1 or any(cvds[0].get(key) != state[key] for key in ("group", "name")):
        raise RuntimeError("Cuttlefish inventory no longer matches this exporter")
    if cvds[0].get("status") != "Running":
        raise RuntimeError("CVD stopped unexpectedly")
    if not set(state["ports"]).issubset(listening_ports()):
        raise RuntimeError("Cuttlefish simulator listener is missing")


def wait_ready(endpoint: str, attempts: int = 60, interval: float = 5) -> None:
    """Startup gate: block until the runtime answers, so the exporter never registers early."""
    state = parse_endpoint(endpoint)
    for _ in range(attempts):
        try:
            if state["backend"] == "exec":
                exec_inventory(state)
            else:
                http_reachable(state)
            return
        except Exception:
            time.sleep(interval)
    raise RuntimeError(f"Cuttlefish runtime at {endpoint} did not become ready")


def listening_ports() -> set[int]:
    # Opening an HCI connection can create a simulator peer. Inspect the shared
    # Pod network namespace instead of disturbing active Bluetooth sessions.
    listeners = set()
    for table in ("/proc/net/tcp", "/proc/net/tcp6"):
        for line in Path(table).read_text().splitlines()[1:]:
            fields = line.split()
            if fields[3] == "0A":
                listeners.add(int(fields[1].rsplit(":", 1)[1], 16))
    return listeners


if __name__ == "__main__":
    try:
        if sys.argv[1] == "--run-exporter":
            initialize(sys.argv[2], sys.argv[3], sys.argv[4])
            os.execvp("jmp", ["jmp", "run", "--exporter-config", sys.argv[5]])
        elif sys.argv[1] == "--wait":
            wait_ready(sys.argv[2])
        else:
            check(sys.argv[1])
    except Exception as exc:
        print(f"Cuttlefish unhealthy: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
