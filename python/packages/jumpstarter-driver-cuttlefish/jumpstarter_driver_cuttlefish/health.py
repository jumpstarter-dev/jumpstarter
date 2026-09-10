"""Pod liveness check using driver intent rather than requiring an always-on guest."""

import json
import os
import sys
import time
import urllib.request
from pathlib import Path


def initialize(state_path: str, runtime_id_path: str, url: str) -> None:
    runtime_id = Path(runtime_id_path).read_text().strip()
    if not runtime_id:
        raise RuntimeError("Cuttlefish runtime ID is empty")
    Path(state_path).write_text(json.dumps({
        "runtime_id_path": runtime_id_path, "runtime_id": runtime_id,
        "url": url, "ports": [], "state": "off",
    }))


def check(state_path: str) -> None:
    state = json.loads(Path(state_path).read_text())
    runtime_id = Path(state["runtime_id_path"]).read_text().strip()
    if not runtime_id or runtime_id != state["runtime_id"]:
        raise RuntimeError("Cuttlefish runtime restarted; release the lease to replace this Pod")
    url = state["url"]
    with urllib.request.urlopen(f"{url}/_debug/statusz", timeout=2):
        pass
    if state["state"] == "off":
        return
    if state["state"] == "transition" and time.monotonic() < state["deadline"]:
        return
    if state["state"] != "running":
        raise RuntimeError("Cuttlefish operation failed or timed out")
    with urllib.request.urlopen(f"{url}/cvds", timeout=2) as response:
        cvds = json.load(response)["cvds"]
    if len(cvds) != 1 or any(cvds[0].get(key) != state[key] for key in ("group", "name")):
        raise RuntimeError("Cuttlefish inventory no longer matches this exporter")
    if cvds[0].get("status") != "Running":
        raise RuntimeError("CVD stopped unexpectedly")
    if not set(state["ports"]).issubset(listening_ports()):
        raise RuntimeError("Cuttlefish simulator listener is missing")


def wait_ready(url: str, attempts: int = 60, interval: float = 5) -> None:
    """Startup gate: block until Host Orchestrator answers, so the exporter never registers early."""
    for _ in range(attempts):
        try:
            with urllib.request.urlopen(f"{url}/_debug/statusz", timeout=3):
                return
        except Exception:
            time.sleep(interval)
    raise RuntimeError(f"Host Orchestrator at {url} did not become ready")


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
