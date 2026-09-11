"""Helpers for driving the ``cvd`` CLI inside the Cuttlefish runtime container.

The exec backend runs ``cvd`` through ``jumpstarter-exec`` over the launcher
socket the provisioner shares between the exporter and the runtime container,
mirroring how Host Orchestrator itself wraps ``cvd`` and how Podcvd controls
per-container instance groups. Kept free of heavy imports so the liveness
probe can use it without loading the driver.
"""

import json
from pathlib import Path
from urllib.parse import urlparse


def exec_binary(launcher_socket: str) -> Path:
    """jumpstarter-exec is staged next to the launcher socket on the shared volume."""
    return Path(launcher_socket).parent / "jumpstarter-exec"


def cvd_argv(launcher_socket: str, user: str, args: list[str]) -> list[str]:
    """Build the argv that runs ``cvd <args>`` in the runtime container.

    ``cvd`` keeps its instance database per uid, so the exec backend must run as
    the same user as any other ``cvd`` caller in the container (Host Orchestrator
    runs as ``httpcvd``). ``runuser`` resets HOME to that user's home directory,
    matching the daemon environment.
    """
    argv = [str(exec_binary(launcher_socket)), "exec", "--socket", launcher_socket, "--"]
    if user:
        argv += ["runuser", "-u", user, "--"]
    return argv + ["cvd", *args]


def parse_endpoint(endpoint: str) -> dict:
    """Describe a managed runtime endpoint for the health state file.

    ``http://127.0.0.1:2081`` selects Host Orchestrator;
    ``exec://httpcvd@/shared/launcher.sock`` selects the cvd CLI over
    jumpstarter-exec, running as the optional user before ``@``.
    """
    parsed = urlparse(endpoint)
    if parsed.scheme == "exec":
        if not parsed.path:
            raise ValueError(f"exec endpoint {endpoint!r} has no socket path")
        return {"backend": "exec", "socket": parsed.path, "cvd_user": parsed.username or ""}
    if parsed.scheme in ("http", "https"):
        return {"backend": "http", "url": endpoint}
    raise ValueError(f"unsupported runtime endpoint {endpoint!r}")


def instance_to_cvd(group_name: str, instance: dict) -> dict:
    """Convert one ``cvd fleet`` instance to the Host Orchestrator CVD object shape.

    Host Orchestrator's ``CvdInstanceToAPIObject`` performs the same mapping, so
    clients see identical documents from both backends.
    """
    return {
        "group": group_name,
        "name": instance.get("instance_name"),
        "status": instance.get("status"),
        "displays": instance.get("displays") or [],
        "webrtc_device_id": instance.get("webrtc_device_id"),
        "adb_serial": instance.get("adb_serial"),
        "adb_port": instance.get("adb_port"),
    }


def group_to_cvds(group: dict) -> list[dict]:
    if not isinstance(group, dict) or not isinstance(group.get("instances"), list):
        raise ValueError(f"unexpected cvd group document: {group!r}")
    return [instance_to_cvd(group.get("group_name", ""), instance) for instance in group["instances"]]


def fleet_to_cvds(output: str) -> list[dict]:
    """Flatten ``cvd fleet`` JSON into a list of Host Orchestrator style CVDs."""
    try:
        data = json.loads(output)
    except ValueError as e:
        raise ValueError(f"cvd fleet returned invalid JSON: {output[:200]!r}") from e
    if not isinstance(data, dict) or not isinstance(data.get("groups"), list):
        raise ValueError(f"unexpected cvd fleet document: {output[:200]!r}")
    cvds = []
    for group in data["groups"]:
        cvds.extend(group_to_cvds(group))
    return cvds


def stderr_tail(text: str, lines: int = 8) -> str:
    """Last lines of cvd stderr, skipping the per-invocation version banner."""
    kept = [line for line in text.strip().splitlines() if "main.cc" not in line or "version:" not in line]
    return "\n".join(kept[-lines:])
