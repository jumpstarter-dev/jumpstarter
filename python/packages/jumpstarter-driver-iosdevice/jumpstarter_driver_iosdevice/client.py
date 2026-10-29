import json
import math
import os
import ssl
import subprocess
import tempfile
from collections.abc import Generator, Sequence
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from functools import partial
from pathlib import Path

import anyio
import click
from jumpstarter_driver_usbflux.adapters import (
    CONNECT_TIMEOUT,
    DISCONNECT_TIMEOUT,
    UsbfluxAdapter,
    forward_endpoint,
    validate_port,
)
from jumpstarter_driver_usbflux.client import CommandGroup, cli_errors, wait_for_interrupt

from jumpstarter.client import DriverClient

PROTOCOLS = ("usbmux", "idb")
MAX_CA_BYTES = 1024 * 1024


def _validate_public_ca(certificate):
    if not isinstance(certificate, str) or len(certificate) > MAX_CA_BYTES or "PRIVATE KEY" in certificate:
        raise ValueError("HTTPS metadata must contain only a public CA certificate")
    try:
        certificate.encode("ascii")
        ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT).load_verify_locations(cadata=certificate)
    except (ValueError, UnicodeError, ssl.SSLError) as exc:
        raise ValueError("HTTPS metadata contains an invalid public CA certificate") from exc


def _copy_https_metadata(metadata):
    def validate(value):
        if isinstance(value, dict):
            if not all(isinstance(key, str) for key in value):
                raise ValueError("HTTPS endpoint metadata must have string keys at every depth")
            for item in value.values():
                validate(item)
        elif isinstance(value, list):
            for item in value:
                validate(item)
        elif value is None or type(value) in (str, bool, int) or (type(value) is float and math.isfinite(value)):
            return
        else:
            raise ValueError("HTTPS endpoint metadata must contain only finite JSON values")

    if not isinstance(metadata, dict):
        raise ValueError("HTTPS endpoint metadata must be a JSON object")  # noqa: TRY004 - Invalid provider data.
    try:
        validate(metadata)
        return json.loads(json.dumps(metadata, allow_nan=False))
    except (RecursionError, OverflowError) as exc:
        raise ValueError("HTTPS endpoint metadata is too deeply nested or cyclic") from exc


@dataclass(frozen=True)
class HttpsEndpoint:
    """A verified HTTPS target and provider metadata, valid inside its context."""

    url: str
    ca_file: Path
    metadata: dict

    def _check_active(self):
        if not self.ca_file.is_file():
            raise RuntimeError("The HTTPS endpoint context has closed")

    def ssl_context(self) -> ssl.SSLContext:
        """Add this endpoint's CA to Python's normal trust and hostname checks."""
        self._check_active()
        context = ssl.create_default_context()
        context.load_verify_locations(cafile=str(self.ca_file))
        return context

    def _trust_file(self, configured: str | None) -> str:
        if not configured:
            return str(self.ca_file)
        path = Path(configured)
        if not path.is_file():
            raise ValueError(f"Configured CA bundle is not a regular file: {path}")
        with path.open("r", encoding="ascii") as source:
            existing = source.read(8 * MAX_CA_BYTES + 1)
        if len(existing) > 8 * MAX_CA_BYTES or "PRIVATE KEY" in existing:
            raise ValueError("Configured CA bundle must contain bounded public certificates only")
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="ascii", dir=self.ca_file.parent, suffix=".pem", delete=False
        ) as f:
            f.write(existing)
            f.write("\n")
            f.write(self.ca_file.read_text(encoding="ascii"))
            return f.name

    def environment(self, base=None) -> dict[str, str]:
        """Copy tool environment and add public trust without disabling TLS.

        Node keeps its built-in roots. Existing explicit Python CA bundles are
        extended, but unset Python trust variables stay unset: Python callers
        can use ``ssl_context()`` or pass ``ca_file`` to their SDK's CA option.
        """
        self._check_active()
        env = dict(os.environ if base is None else base)
        if env.get("NODE_TLS_REJECT_UNAUTHORIZED", "").strip() == "0":
            raise ValueError("NODE_TLS_REJECT_UNAUTHORIZED=0 disables HTTPS verification; remove it first")
        env["NODE_EXTRA_CA_CERTS"] = self._trust_file(env.get("NODE_EXTRA_CA_CERTS"))
        for name in ("SSL_CERT_FILE", "REQUESTS_CA_BUNDLE"):
            if env.get(name):
                env[name] = self._trust_file(env[name])
        env["JUMPSTARTER_IOS_HTTPS_URL"] = self.url
        env["JUMPSTARTER_IOS_HTTPS_CA_FILE"] = str(self.ca_file)
        env["JUMPSTARTER_IOS_HTTPS_METADATA"] = json.dumps(self.metadata, allow_nan=False)
        return env


class IosDeviceClient(DriverClient):
    def info(self) -> dict:
        info = self.call("info")
        # Generic RPC values carry every number as a float; ports are integers.
        ports = info.get("forward_ports")
        if isinstance(ports, list):
            info["forward_ports"] = [
                int(port) if isinstance(port, float) and port.is_integer() else port for port in ports
            ]
        return info

    @contextmanager
    def https(self, *, port: int = 0) -> Generator[HttpsEndpoint, None, None]:
        """Expose the configured HTTPS service with its public CA and metadata.

        The client does not interpret provider metadata or configure tools.
        Closing the context removes its listener and CA files. The exporter
        owns service resources until lease teardown or reset.
        """
        validate_port(port, ephemeral=True)
        envelope = self.call("https_info")
        if not isinstance(envelope, dict):
            raise TypeError("Invalid HTTPS endpoint response")
        certificate = envelope.get("ca_certificate")
        _validate_public_ca(certificate)
        metadata = _copy_https_metadata(envelope.get("metadata"))
        with tempfile.TemporaryDirectory(prefix="jumpstarter-ios-https-") as directory:
            ca_file = Path(directory) / "ca.pem"
            ca_file.write_text(certificate, encoding="ascii")
            ca_file.chmod(0o600)
            with forward_endpoint(
                client=self, method="connect_https", local_host="127.0.0.1", local_port=port
            ) as address:
                yield HttpsEndpoint(url=f"https://{address}", ca_file=ca_file, metadata=metadata)

    def _protocol(self, protocol: str | None) -> str:
        info = self.info()
        supported = info.get("protocols", [])
        if protocol is None:
            protocol = supported[0] if supported else None
        if not isinstance(protocol, str) or protocol not in supported:
            names = ", ".join(supported) or "none"
            raise ValueError(f"Unsupported protocol {protocol!r}; supported protocols: {names}")
        if protocol not in PROTOCOLS:
            names = ", ".join(PROTOCOLS)
            raise ValueError(f"Protocol {protocol!r} is not implemented by this client; supported: {names}")
        if info.get("present") is False:
            raise RuntimeError(info.get("reason") or "The iOS target is not present")
        return protocol

    @contextmanager
    def serve(self, protocol: str | None = None, *, port: int = 0) -> Generator[str, None, None]:
        """Serve a protocol on IPv4 loopback without invoking device tools.

        The native protocol is the first one advertised by the target. ``port=0``
        selects an unused local port. The yielded endpoint is ``127.0.0.1:port``.
        """
        protocol = self._protocol(protocol)
        validate_port(port, ephemeral=True)
        with forward_endpoint(
            client=self,
            method=f"connect_{protocol}",
            local_host="127.0.0.1",
            local_port=port,
        ) as endpoint:
            yield endpoint

    @contextmanager
    def connect(
        self,
        protocol: str | None = None,
        *,
        port: int = 0,
        usbfluxctl: str = "usbfluxctl",
        idb: str = "idb",
        timeout: float = CONNECT_TIMEOUT,
    ) -> Generator[str, None, None]:
        """Attach with the user's usbfluxctl or idb and detach on exit.

        The user installs and runs usbfluxd before attaching a usbmux endpoint.
        Cleanup also covers a timed out or failed attach, and stays bounded.
        """
        protocol = self._protocol(protocol)
        if protocol == "usbmux":
            with UsbfluxAdapter(
                client=self, method="connect_usbmux", port=port, usbfluxctl=usbfluxctl, timeout=timeout
            ) as target:
                yield target
            return
        with self.serve(protocol, port=port) as target:
            host, local_port = target.rsplit(":", 1)
            attach = [idb, "connect", host, local_port]
            detach = [idb, "disconnect", host, local_port]
            try:
                try:
                    result = subprocess.run(attach, check=False, capture_output=True, text=True, timeout=timeout)
                except (subprocess.SubprocessError, OSError) as exc:
                    raise RuntimeError(f"Could not run {attach[0]} to attach {target}: {exc}") from exc
                if result.returncode:
                    message = (result.stderr or result.stdout or "no output").strip()
                    raise RuntimeError(f"{attach[0]} failed to attach {target}: {message}")
                yield target
            finally:
                try:
                    result = subprocess.run(
                        detach, check=False, capture_output=True, text=True, timeout=DISCONNECT_TIMEOUT
                    )
                    if result.returncode:
                        self.logger.warning("Could not detach %s: %s", target, result.stderr or result.stdout)
                except (subprocess.SubprocessError, OSError) as exc:
                    self.logger.warning("Could not detach %s: %s", target, exc)

    @contextmanager
    def forward(self, port: int, *, local_port: int | None = None) -> Generator[str, None, None]:
        """Forward an exporter-configured device port, binding only loopback.

        The local port defaults to the device port; use zero for an unused port.
        """
        validate_port(port)
        local_port = port if local_port is None else local_port
        validate_port(local_port, ephemeral=True)
        child = self.children.get(f"port_{port}")
        if child is None:
            configured = ", ".join(str(value) for value in self.info().get("forward_ports", [])) or "none"
            raise ValueError(f"Port {port} is not configured on this exporter; configured ports: {configured}")
        with forward_endpoint(client=child, local_host="127.0.0.1", local_port=local_port) as endpoint:
            yield endpoint

    def run(self, command: Sequence[str], protocol: str | None = None) -> int:
        """Run a local command with its transport endpoint in the environment."""
        if not command:
            raise ValueError("Provide a command to run")
        protocol = self._protocol(protocol)
        with self.serve(protocol) as target:
            env = os.environ.copy()
            env["USBMUXD_SOCKET_ADDRESS" if protocol == "usbmux" else "IDB_COMPANION"] = target
            # Lease cancellation must stop the process before closing its transport.
            result = self.portal.call(
                partial(anyio.run_process, list(command), env=env, stdin=None, stdout=None, stderr=None, check=False)
            )
            return result.returncode

    def cli(self):  # noqa: C901
        @click.group(cls=CommandGroup, invoke_without_command=True)
        @click.option("--protocol", help="Protocol to use (defaults to the target's native protocol)")
        @click.pass_context
        def ios(ctx, protocol):
            """One iOS device. Run your own tool with: ios [--protocol NAME] -- COMMAND."""
            command = ctx.meta.get(CommandGroup.meta_key)
            if command is not None and ctx.invoked_subcommand != "https":
                if ctx.invoked_subcommand is not None:
                    raise click.UsageError("Use ios -- COMMAND without a subcommand", ctx)
                with cli_errors():
                    returncode = self.run(command, protocol)
                ctx.exit(returncode if returncode >= 0 else 128 - returncode)
            if ctx.invoked_subcommand is None:
                click.echo(ctx.get_help())

        @ios.command()
        @click.option("-P", "--port", type=click.IntRange(0, 65535), default=0, help="Local HTTPS port (0=auto)")
        @click.pass_context
        def https(ctx, port):
            """Expose HTTPS or run a command with its public CA trust environment."""
            with cli_errors(), self.https(port=port) as endpoint:
                command = ctx.parent.meta.get(CommandGroup.meta_key)
                if command is None:
                    click.echo(
                        json.dumps(
                            {
                                "url": endpoint.url,
                                "ca_file": str(endpoint.ca_file),
                                "metadata": endpoint.metadata,
                            }
                        )
                    )
                    wait_for_interrupt(self)
                    return
                result = self.portal.call(
                    partial(
                        anyio.run_process,
                        command,
                        env=endpoint.environment(),
                        stdin=None,
                        stdout=None,
                        stderr=None,
                        check=False,
                    )
                )
            ctx.exit(result.returncode if result.returncode >= 0 else 128 - result.returncode)

        @ios.command()
        def info():
            """Show identity, presence and supported protocols."""
            for key, value in self.info().items():
                click.echo(f"{key}: {value}")

        @ios.command()
        @click.option("--protocol", help="Protocol to expose")
        @click.option("-P", "--port", type=click.IntRange(0, 65535), default=0, help="Local port (0=auto)")
        @click.pass_context
        def serve(ctx, protocol, port):
            """Serve a local endpoint for your own tools until Ctrl+C."""
            with cli_errors():
                protocol = self._protocol(protocol or ctx.parent.params["protocol"])
                with self.serve(protocol, port=port) as target:
                    click.echo(target)
                    if protocol == "usbmux":
                        click.echo(f"export USBMUXD_SOCKET_ADDRESS={target}")
                    else:
                        host, local_port = target.rsplit(":", 1)
                        click.echo(f"idb connect {host} {local_port}")
                        click.echo(f"export IDB_COMPANION={target}")
                    click.echo("Press Ctrl+C to stop")
                    wait_for_interrupt(self)

        @ios.command()
        @click.option("--protocol", help="Protocol to attach")
        @click.option("-P", "--port", type=click.IntRange(0, 65535), default=0, help="Local port (0=auto)")
        @click.option("--usbfluxctl", default="usbfluxctl", show_default=True, help="Path to your local usbfluxctl")
        @click.option("--idb", default="idb", show_default=True, help="Path to your local idb")
        @click.pass_context
        def connect(ctx, protocol, port, usbfluxctl, idb):
            """Attach to your own usbfluxd or idb until Ctrl+C."""
            with cli_errors():
                with self.connect(
                    protocol or ctx.parent.params["protocol"], port=port, usbfluxctl=usbfluxctl, idb=idb
                ) as target:
                    click.echo(f"Connected at {target}")
                    click.echo("Press Ctrl+C to disconnect")
                    wait_for_interrupt(self)
                click.echo("Disconnected")

        @ios.command()
        @click.argument("ports", nargs=-1, type=click.IntRange(1, 65535), required=True)
        def forward(ports):
            """Forward configured device PORTS on matching local loopback ports."""
            with cli_errors(), ExitStack() as stack:
                for port in dict.fromkeys(ports):
                    target = stack.enter_context(self.forward(port))
                    click.echo(f"{target} -> device:{port}")
                click.echo("Press Ctrl+C to stop")
                wait_for_interrupt(self)

        return ios
