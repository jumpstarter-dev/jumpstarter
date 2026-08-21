import json
import threading
import time

import click
from jumpstarter_driver_composite.client import CompositeClient
from jumpstarter_driver_power.client import VirtualPowerClient

from jumpstarter.client.base import StubDriverClient


def _parse(raw: str) -> dict | list | str:
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return raw


def _echo(obj) -> None:
    if isinstance(obj, (dict, list)):
        click.echo(json.dumps(obj, indent=2))
    else:
        click.echo(obj)


def _run_with_progress(label: str, fn):
    result = [None]
    error = [None]

    def worker():
        try:
            result[0] = fn()
        except Exception as e:
            error[0] = e

    t = threading.Thread(target=worker)
    t.start()
    start = time.time()
    click.echo(f"{label}...", nl=False)
    while t.is_alive():
        t.join(timeout=10)
        if t.is_alive():
            elapsed = int(time.time() - start)
            click.echo(f" {elapsed}s", nl=False)
    elapsed = int(time.time() - start)
    if error[0] is not None:
        click.echo(f" failed ({elapsed}s)")
        raise error[0]
    click.echo(f" done ({elapsed}s)")
    return result[0]


class CvdPowerClient(VirtualPowerClient):
    def cli(self):
        @click.group()
        def power():
            """CVD power control."""

        @power.command()
        def on():
            """Power on (create or start CVD, wait for boot)."""
            _run_with_progress("Powering on", self.on)

        @power.command()
        @click.option("--destroy", is_flag=True, help="Delete CVD entirely after stopping")
        def off(destroy: bool):
            """Power off (stop CVD)."""
            _run_with_progress("Powering off", lambda: self.off(destroy))

        @power.command()
        @click.option("--wait", "-w", default=2, type=click.IntRange(min=0), help="Seconds between off and on")
        def cycle(wait: int):
            """Power cycle."""
            _run_with_progress("Power cycling", lambda: self.cycle(wait))

        return power


class CuttlefishClient(CompositeClient):
    """Client for controlling Cuttlefish Host Orchestrator with nested children."""

    def list_cvds(self) -> dict | list | str:
        return _parse(self.call("list_cvds"))

    def get_cvd(self) -> dict | list | str:
        return _parse(self.call("get_cvd"))

    def restart_cvd(self) -> dict | list | str:
        return _parse(self.call("restart_cvd"))

    def powerwash_cvd(self) -> dict | list | str:
        return _parse(self.call("powerwash_cvd"))

    def powerbtn_cvd(self) -> dict | list | str:
        return _parse(self.call("powerbtn_cvd"))

    def get_host(self) -> str:
        return self.call("get_host")

    def get_webrtc_url(self) -> str:
        return self.call("get_webrtc_url")

    def status(self) -> str:
        return self.call("status")

    def list_operations(self) -> dict | list | str:
        return _parse(self.call("list_operations"))

    def reset_host(self) -> dict | list | str:
        return _parse(self.call("reset_host"))

    def wait_boot(self, timeout: int = 0) -> str:
        return self.call("wait_boot", timeout)

    def cli(self):  # noqa: C901
        @click.group()
        def cuttlefish():
            """Cuttlefish Host Orchestrator.

            Manage CVD lifecycle via power/storage children,
            plus cuttlefish-specific operations (powerwash, etc.).
            """

        @cuttlefish.command("list")
        def list_cmd():
            """List all CVDs."""
            _echo(self.list_cvds())

        @cuttlefish.command("get")
        def get_cmd():
            """Get details for this CVD."""
            _echo(self.get_cvd())

        @cuttlefish.command("restart")
        def restart_cmd():
            """Restart the CVD."""
            _echo(_run_with_progress("Restarting CVD", lambda: self.restart_cvd()))

        @cuttlefish.command("powerwash")
        def powerwash_cmd():
            """Factory reset the CVD."""
            _echo(_run_with_progress("Powerwashing CVD", lambda: self.powerwash_cvd()))

        @cuttlefish.command("powerbtn")
        def powerbtn_cmd():
            """Simulate power button press."""
            _echo(self.powerbtn_cvd())

        @cuttlefish.command("status")
        def status_cmd():
            """Health check."""
            click.echo(self.status())

        @cuttlefish.command("ops")
        def ops_cmd():
            """List running operations."""
            _echo(self.list_operations())

        @cuttlefish.command("wait-boot")
        @click.option("--timeout", default=0, type=int, help="Timeout in seconds (0 = use boot_timeout config)")
        def wait_boot_cmd(timeout: int):
            """Wait for CVD to finish booting."""
            click.echo(_run_with_progress("Waiting for boot", lambda: self.wait_boot(timeout)))

        @cuttlefish.command("reset")
        def reset_cmd():
            """Delete all CVDs and clean up stale state."""
            _echo(_run_with_progress("Resetting", lambda: self.reset_host()))

        @cuttlefish.command("webrtc")
        def webrtc_cmd():
            """Print the WebRTC display URL."""
            click.echo(self.get_webrtc_url())

        for k, v in self.children.items():
            if isinstance(v, StubDriverClient):
                continue
            if not hasattr(v, "cli"):
                continue
            cuttlefish.add_command(v.cli(), k)

        return cuttlefish
