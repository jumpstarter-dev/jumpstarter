#!/usr/bin/env python3
"""Exercise installed native clients against explicitly supplied E2E fixtures.

The exporter must expose MockPower and an echo network. Other fixture drivers
may be present; clients without Windows support load them as stubs. This runner
toggles the fixture's power and creates short controller leases. It
does not provision infrastructure or save credentials. Managed connection errors
are failures, including errors in the native Windows Unix listener.
Each probe runs in a subprocess so a transport or teardown hang has a deadline.
"""

import argparse
import json
import os
import platform
import signal
import socket
import subprocess
import sys
import tempfile
import time
from contextlib import ExitStack, contextmanager
from datetime import UTC, datetime, timedelta
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

ALLOWED_CLIENTS = [
    "jumpstarter_driver_composite.client.CompositeClient",
    "jumpstarter_driver_network.client.NetworkClient",
    "jumpstarter_driver_power.client.PowerClient",
]
PAYLOAD = bytes(range(256)) * 4 + b"\x00\xff\x1a\r\nwindows-e2e"
RESULT_PREFIX = "WINDOWS_E2E_RESULT="
DIRECT_PROBES = (
    "direct.discovery", "direct.power", "direct.binary_streams", "direct.tcp_forwarding",
    "direct.cli_help", "direct.cli_power", "direct.shell_command",
)
CONTROLLER_PROBES = (
    "controller.discovery", "controller.lease_lifecycle", "controller.managed_connection", "controller.shell_command",
)
CLI_PROBES = {"direct.cli_help", "direct.cli_power", "direct.shell_command", "controller.shell_command"}
POWERSHELL_PROBES = ("direct.powershell", "controller.powershell")
CLI_PROBES.update(POWERSHELL_PROBES)


def arguments():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--direct-endpoint", help="Linux exporter's published host:port")
    parser.add_argument("--direct-insecure", action="store_true", help="Use plaintext gRPC for the direct fixture")
    parser.add_argument("--controller-config", type=Path, help="Existing ClientConfig YAML, read only")
    parser.add_argument("--exporter-name", default="windows-e2e-linux", help="Controller-managed Linux exporter")
    parser.add_argument("--power-driver", default="power")
    parser.add_argument("--network-driver", default="network")
    parser.add_argument("--timeout", type=float, default=30, help="Probe timeout in seconds (minimum 5)")
    parser.add_argument("--report", type=Path, help="Write a JSON results report, never a credential file")
    parser.add_argument("--powershell", help="Also exercise interactive startup with this pwsh/powershell executable")
    parser.add_argument("--_probe", choices=(*DIRECT_PROBES, *CONTROLLER_PROBES, *POWERSHELL_PROBES),
                        help=argparse.SUPPRESS)
    args = parser.parse_args()
    if not args.direct_endpoint and not args.controller_config:
        parser.error("provide --direct-endpoint, --controller-config, or both")
    if not 5 <= args.timeout <= 600:
        parser.error("--timeout must be between 5 and 600 seconds")
    if args.controller_config and not args.controller_config.is_file():
        parser.error("--controller-config must name an existing file")
    if args.controller_config:
        import yaml

        try:
            config = yaml.safe_load(args.controller_config.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError):
            parser.error("--controller-config must be a readable YAML mapping")
        if not isinstance(config, dict):
            parser.error("--controller-config must be a YAML mapping")
        if config.get("refresh_token"):
            parser.error(
                "use an internal-token fixture config without refresh_token; shell may persist refreshed tokens"
            )
    if args.report and args.controller_config and args.report.resolve() == args.controller_config.resolve():
        parser.error("--report must not overwrite --controller-config")
    return args


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def wait_for_lease_release(config, names=None):
    # DeleteLease requests release; the controller reconciles active status
    # asynchronously. A successful RPC does not guarantee an immediate list
    # reflects it. Preserve the cleanup assertion with a bounded observation.
    started = time.monotonic()
    while True:
        active = {item.name for item in config.list_leases().leases}
        pending = active if names is None else active.intersection(names)
        if not pending:
            return round(time.monotonic() - started, 3)
        require(time.monotonic() - started < 5, f"lease remains active after cleanup deadline: {sorted(pending)}")
        time.sleep(0.1)


def isolated_environment():
    # Never inherit an unrelated active lease or client identity from a shell.
    env = {key: value for key, value in os.environ.items()
           if not key.startswith(("JUMPSTARTER_", "JMP_"))}
    return {**env, "PYTHONUTF8": "1"}


@contextmanager
def direct_client(args):
    from anyio.from_thread import start_blocking_portal

    from jumpstarter.client import client_from_path

    with (
        start_blocking_portal() as portal,
        ExitStack() as stack,
        portal.wrap_async_context_manager(client_from_path(
            args.direct_endpoint, portal, stack, allow=ALLOWED_CLIENTS,
            unsafe=False, insecure=args.direct_insecure,
        )) as client,
    ):
        yield client


def child(client, name):
    require(name in client.children, f"required fixture driver {name!r} is absent")
    return client.children[name]


def check_power(client, args):
    power = child(client, args.power_driver)
    try:
        power.on()
        readings = list(power.read())
        require(bool(readings), "power read returned no measurements")
    finally:
        power.off()
    return {"readings": len(readings), "power_off_completed": True}


def stream_drivers(args):
    return (args.network_driver,)


def check_streams(client, args):
    import anyio

    async def exchange(stream_client):
        with anyio.fail_after(args.timeout):
            async with stream_client.stream_async("connect") as stream:
                await stream.send(PAYLOAD)
                received = bytearray()
                while len(received) < len(PAYLOAD):
                    received.extend(await stream.receive())
                require(received == PAYLOAD, "binary stream payload mismatch")

    for name in stream_drivers(args):
        client.portal.call(exchange, child(client, name))
    return {"bytes_per_stream": len(PAYLOAD), "drivers": list(stream_drivers(args))}


def check_forwarding(client, args):
    from jumpstarter_driver_network.adapters import TcpPortforwardAdapter

    for name in stream_drivers(args):
        with (
            TcpPortforwardAdapter(client=child(client, name)) as address,
            socket.create_connection(address, timeout=args.timeout) as stream,
        ):
            stream.sendall(PAYLOAD)
            received = bytearray()
            while len(received) < len(PAYLOAD):
                chunk = stream.recv(len(PAYLOAD) - len(received))
                require(bool(chunk), "forwarded stream closed before completing payload")
                received.extend(chunk)
            require(received == PAYLOAD, "forwarded binary payload mismatch")
        try:
            connection = socket.create_connection(address, timeout=1)
        except OSError:
            pass
        else:
            connection.close()
            raise AssertionError("local forwarding listener survived context exit")
    return {"bytes_per_forward": len(PAYLOAD), "listeners_closed": True}


def terminate_process_tree(process):
    """Kill only the process tree started for this probe, including nested j."""
    if sys.platform == "win32":
        cleanup = subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            capture_output=True, text=True, timeout=10, check=False,
        )
        if cleanup.returncode != 0:
            if process.poll() is None:
                process.kill()
            raise RuntimeError(f"taskkill could not confirm process-tree cleanup: {cleanup.stderr or cleanup.stdout}")
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


def run_bounded(command, env, timeout, input_text=None):
    options = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if sys.platform == "win32" else {
        "start_new_session": True,
    }
    # Files avoid PIPE reader threads that can hang if a descendant keeps a
    # handle open after its parent exits or process-tree cleanup is rejected.
    with (
        tempfile.TemporaryFile() as stdout_file,
        tempfile.TemporaryFile() as stderr_file,
        tempfile.TemporaryFile() as stdin_file,
    ):
        if input_text is not None:
            stdin_file.write(input_text.encode("utf-8"))
            stdin_file.seek(0)
        process = subprocess.Popen(
            command, env=env, stdout=stdout_file, stderr=stderr_file,
            stdin=stdin_file if input_text is not None else None, **options,
        )
        try:
            process.wait(timeout=timeout)
        except BaseException as exc:
            try:
                terminate_process_tree(process)
            finally:
                if process.poll() is None:
                    process.kill()
                process.wait(timeout=5)
            if isinstance(exc, subprocess.TimeoutExpired):
                stdout_file.seek(0)
                stderr_file.seek(0)
                exc.output = stdout_file.read().decode("utf-8", errors="replace")
                exc.stderr = stderr_file.read().decode("utf-8", errors="replace")
            raise
        stdout_file.seek(0)
        stderr_file.seek(0)
        stdout = stdout_file.read().decode("utf-8", errors="replace")
        stderr = stderr_file.read().decode("utf-8", errors="replace")
    return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)


def cli_probe(args, probe):
    env = isolated_environment()
    env["JMP_DRIVERS_ALLOW"] = ",".join(ALLOWED_CLIENTS)
    driver_command = [sys.executable, "-m", "jumpstarter_cli.j", args.power_driver, "read", "--count", "1"]
    timeout = args.timeout
    input_text = None
    powershell = probe in POWERSHELL_PROBES
    if probe.endswith("shell_command") or powershell:
        command = [sys.executable, "-m", "jumpstarter_cli.jmp", "shell"]
        if probe.startswith("direct."):
            command.extend(["--tls-grpc", args.direct_endpoint])
            if args.direct_insecure:
                command.append("--tls-grpc-insecure")
        else:
            command.extend([
                "--client-config", str(args.controller_config.resolve()), "--name", args.exporter_name,
                "--duration", f"{max(120, int(args.timeout) + 35)}s",
                "--acquisition-timeout", f"{args.timeout:g}s", "--retry-timeout", "0",
                "--dial-timeout", f"{args.timeout:g}s",
            ])
            timeout += 35  # Allow the stock lease's shielded cleanup to complete.
        if powershell:
            require(bool(args.powershell), "--powershell is required for this probe")
            env.update({
                "SHELL": args.powershell, "NO_COLOR": "1", "NO_ICONS": "1",
                # Mirror uv/venv activation when this runner is invoked directly
                # with a Python executable, so the actual j entry point is used.
                "PATH": str(Path(sys.executable).parent) + os.pathsep + env.get("PATH", ""),
                "_E2E_POWER_DRIVER": args.power_driver,
                "_E2E_EXPORTER": "direct" if probe.startswith("direct.") else args.exporter_name,
            })
            # No custom COMMAND: exercise the stock interactive shell bootstrap.
            # Data stays in environment variables, outside PowerShell source.
            input_text = """\
if (-not $env:JUMPSTARTER_HOST -or -not $env:JMP_LEASE) { exit 81 }
if ($env:JMP_EXPORTER -ne $env:_E2E_EXPORTER) { exit 82 }
if (-not (prompt).Contains($env:_E2E_EXPORTER)) { exit 83 }
j --help
if ($LASTEXITCODE -ne 0) { exit 84 }
j $env:_E2E_POWER_DRIVER read --count 1
if ($LASTEXITCODE -ne 0) { exit 85 }
Write-Output ('POWERSHELL_SESSION_OK=' + $env:JUMPSTARTER_HOST)
exit 37
"""
        else:
            command.extend(["--", *driver_command])
    else:
        env.update({
            "JUMPSTARTER_HOST": args.direct_endpoint,
            "JMP_GRPC_INSECURE": "1" if args.direct_insecure else "0",
        })
        command = ([sys.executable, "-m", "jumpstarter_cli.j", "--help"]
                   if probe == "direct.cli_help" else driver_command)
    # opt_config may create UserConfig even with explicit exporter/client flags.
    # Exercise that stock behavior in a fresh directory, never the user's config.
    with tempfile.TemporaryDirectory(prefix="jumpstarter-windows-e2e-") as config_home:
        env["JMP_CLIENT_CONFIG_HOME"] = config_home
        completed = run_bounded(command, env, timeout, input_text)
    require(
        completed.returncode == (37 if powershell else 0),
        f"CLI failed ({completed.returncode}): {(completed.stdout + completed.stderr)[-6000:]}",
    )
    expected = (args.power_driver, *stream_drivers(args)) if probe == "direct.cli_help" else ("voltage=",)
    require(all(item in completed.stdout for item in expected), "j output is missing expected fixture information")
    if powershell:
        # Match output lines, not the redirected input echoed by PowerShell.
        hosts = [line.removeprefix("POWERSHELL_SESSION_OK=").strip() for line in completed.stdout.splitlines()
                 if line.startswith("POWERSHELL_SESSION_OK=")]
        require(len(hosts) == 1, "PowerShell did not complete both child commands")
        if probe.startswith("controller."):
            require(not Path(hosts[0]).exists(), "managed shell socket survived exit")
            require(not Path(hosts[0]).parent.exists(), "managed shell socket directory survived exit")
            wait_for_lease_release(load_controller(args))
    return {"command": command, "returncode": completed.returncode}


def load_controller(args):
    from jumpstarter.config.client import ClientConfigV1Alpha1

    config = ClientConfigV1Alpha1.from_file(args.controller_config)
    config.drivers.allow = ALLOWED_CLIENTS
    config.drivers.unsafe = False
    config.leases.acquisition_timeout = int(args.timeout)
    config.leases.dial_timeout = args.timeout
    config.leases.retry_timeout = 0
    return config


def controller_probe(args, probe):
    config = load_controller(args)
    if probe == "controller.discovery":
        exporters = config.list_exporters().exporters
        selected = next((item for item in exporters if item.name == args.exporter_name), None)
        require(selected is not None, "configured exporter is absent from controller discovery")
        require(selected.online, "configured exporter is offline")
        return {"exporter": selected.name, "online": selected.online, "exporter_count": len(exporters)}

    with config.lease(
        exporter_name=args.exporter_name, duration=timedelta(seconds=max(120, int(args.timeout) + 35)),
    ) as lease:
        lease_name = lease.name
        if probe == "controller.managed_connection":
            # Deliberately exercise the stock routed connection, including its
            # local listener. Do not substitute a TCP bridge or monkeypatch it.
            with lease.connect() as client:
                check_power(client, args)
                check_streams(client, args)
    release_wait = wait_for_lease_release(config, {lease_name})
    return {"lease": lease_name, "released": True, "exporter": args.exporter_name,
            "release_observation_seconds": release_wait}


def run_probe(args, probe):
    if probe in CLI_PROBES:
        return cli_probe(args, probe)
    if probe.startswith("controller."):
        return controller_probe(args, probe)
    with direct_client(args) as client:
        if probe == "direct.discovery":
            for name in (args.power_driver, *stream_drivers(args)):
                child(client, name)
            return {"drivers": sorted(client.children)}
        checks = {
            "direct.power": check_power,
            "direct.binary_streams": check_streams,
            "direct.tcp_forwarding": check_forwarding,
        }
        return checks[probe](client, args)


def exception_details(exc):
    if isinstance(exc, BaseExceptionGroup):
        return [item for nested in exc.exceptions for item in exception_details(nested)]
    return [{"type": type(exc).__name__, "message": str(exc)[:2000]}]


def redact(value, args):
    text = json.dumps(value, ensure_ascii=False)
    if args.controller_config:
        import yaml

        try:
            config = yaml.safe_load(args.controller_config.read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError):
            config = {}
        if not isinstance(config, dict):
            config = {}
        for field in ("token", "refresh_token"):
            secret = config.get(field)
            if isinstance(secret, str) and secret:
                text = text.replace(secret, "<redacted>")
    return text


def worker(args):
    started = time.monotonic()
    result = {"probe": args._probe}
    try:
        result.update(status="PASS", details=run_probe(args, args._probe))
    except Exception as exc:  # noqa: BLE001 - preserve every probe failure in the report
        result.update(status="FAIL", errors=exception_details(exc))
    result["seconds"] = round(time.monotonic() - started, 3)
    print(RESULT_PREFIX + redact(result, args), flush=True)
    return 0 if result["status"] == "PASS" else 1


def probe_command(args, probe):
    command = [sys.executable, str(Path(__file__).resolve()), "--_probe", probe,
               "--timeout", str(args.timeout), "--exporter-name", args.exporter_name,
               "--power-driver", args.power_driver, "--network-driver", args.network_driver]
    if args.direct_endpoint:
        command.extend(["--direct-endpoint", args.direct_endpoint])
    if args.direct_insecure:
        command.append("--direct-insecure")
    if args.controller_config:
        command.extend(["--controller-config", str(args.controller_config.resolve())])
    return command


def main():
    args = arguments()
    if args._probe:
        return worker(args)
    results = []
    probes = (*DIRECT_PROBES, *CONTROLLER_PROBES, *(POWERSHELL_PROBES if args.powershell else ()))
    for probe in probes:
        if (probe.startswith("direct.") and not args.direct_endpoint
                or probe.startswith("controller.") and not args.controller_config):
            results.append({"probe": probe, "status": "SKIP", "reason": "endpoint/config not supplied"})
            continue
        # Stock lease cleanup is shielded and has its own 30-second deadline.
        timeout = args.timeout + (35 if probe.startswith("controller.") else 3)
        started = time.monotonic()
        try:
            if probe in CLI_PROBES:
                # Run the CLI itself as the supervised process, avoiding a
                # disposable worker parent above its nested shell/j command.
                result = {"probe": probe, "status": "PASS", "details": cli_probe(args, probe)}
            else:
                completed = run_bounded(probe_command(args, probe), isolated_environment(), timeout)
                lines = [line.removeprefix(RESULT_PREFIX) for line in completed.stdout.splitlines()
                         if line.startswith(RESULT_PREFIX)]
                if lines:
                    result = json.loads(lines[-1])
                    require(isinstance(result, dict), "worker result must be an object")
                    require(result.get("probe") == probe, "worker result has the wrong probe name")
                    require(result.get("status") in ("PASS", "FAIL"), "worker result has an invalid status")
                    expected_exit = 0 if result["status"] == "PASS" else 1
                    require(completed.returncode == expected_exit, "worker result contradicts its exit status")
                else:
                    result = {"probe": probe, "status": "FAIL", "errors": [{
                        "type": "WorkerExit", "message": completed.stderr[-2000:],
                        "returncode": completed.returncode,
                    }]}
        except Exception as exc:  # noqa: BLE001 - CLI and watchdog failures must be recorded too
            result = {"probe": probe, "status": "FAIL", "errors": exception_details(exc)}
        result.setdefault("seconds", round(time.monotonic() - started, 3))
        results.append(result)
        print(f"{result['status']}: {probe}", flush=True)
    try:
        installed_version = version("jumpstarter")
    except PackageNotFoundError:
        installed_version = "not installed"
    report = {
        "recorded_at": datetime.now(UTC).isoformat(),
        "platform": platform.platform(), "python": sys.version, "jumpstarter": installed_version,
        "direct_endpoint": args.direct_endpoint,
        "controller_config": str(args.controller_config) if args.controller_config else None,
        "exporter_name": args.exporter_name, "results": results,
        "powershell": args.powershell,
        "limitations": [
            "MockPower and the echo network validate transport, not physical hardware.",
            "Driver-specific clients are covered by the runners added with their Windows support.",
            "Optional PowerShell probes exercise interactive startup with redirected input, not a real terminal.",
            "Managed connection failures are reported unchanged; no socket compatibility shim is applied.",
        ],
    }
    output = redact(report, args)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(output + "\n", encoding="utf-8")
    print(output)
    return 1 if any(item["status"] == "FAIL" for item in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
