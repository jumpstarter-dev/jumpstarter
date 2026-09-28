#!/usr/bin/env python3
"""Qualify native Windows OpenSSH against a local, temporary SSH simulator.

Runs stock SSHWrapperClient through Jumpstarter TCP forwarding. The fixture
accepts only an ephemeral generated public key, never executes a received command,
and listens on loopback. No user SSH configuration or authorized_keys is changed.
Each probe runs in a bounded child; generated identities stay in its temporary
directory. Requires installed Jumpstarter SSH/network packages and Paramiko.
"""

import argparse
import io
import json
import os
import platform
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from contextlib import contextmanager
from pathlib import Path

PROBES = (
    "default_binary",
    "default_text",
    "nonzero_exit",
    "remote_command_quoting",
    "quoted_executable",
    "unquoted_executable",
)
RESULT_PREFIX = "WINDOWS_SSH_RESULT="
PAYLOAD = "OpenSSH fixture: café λ\n".encode()


def arguments():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ssh", type=Path, help="Native ssh.exe; defaults to PATH lookup")
    parser.add_argument("--output", type=Path, help="Write JSON qualification results")
    parser.add_argument("--timeout", type=float, default=30, help="Deadline per probe in seconds")
    parser.add_argument("--probe", choices=PROBES, help="Run only one probe")
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--workdir", type=Path, help=argparse.SUPPRESS)
    return parser.parse_args()


@contextmanager
def ssh_fixture(public_key, exit_status):  # noqa: C901
    import paramiko

    stop = threading.Event()
    command_ready = threading.Event()
    state = {"commands": [], "authenticated_users": [], "authentication_complete": False, "errors": []}
    transports = []
    host_key = paramiko.RSAKey.generate(2048)

    class Server(paramiko.ServerInterface):
        def get_allowed_auths(self, username):
            return "publickey"

        def check_auth_publickey(self, username, key):
            if username == "fixture-user" and key.asbytes() == public_key.asbytes():
                state["authenticated_users"].append(username)
                return paramiko.AUTH_SUCCESSFUL
            return paramiko.AUTH_FAILED

        def check_channel_request(self, kind, channel_id):
            return paramiko.OPEN_SUCCEEDED if kind == "session" else paramiko.OPEN_FAILED_ADMINISTRATIVELY_PROHIBITED

        def check_channel_exec_request(self, channel, command):
            state["commands"].append(command.decode("utf-8"))
            command_ready.set()
            return True

    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    listener.settimeout(0.2)
    address = listener.getsockname()

    def run():
        try:
            while not stop.is_set():
                try:
                    connection, _ = listener.accept()
                except TimeoutError:
                    continue
                transport = paramiko.Transport(connection)
                transports.append(transport)
                transport.add_server_key(host_key)
                transport.start_server(server=Server())
                channel = transport.accept(timeout=10)
                state["authentication_complete"] = transport.is_authenticated()
                if channel is None or not command_ready.wait(10):
                    raise RuntimeError("OpenSSH did not request a command")
                channel.sendall(PAYLOAD)
                channel.sendall_stderr(b"fixture stderr\n")
                channel.send_exit_status(exit_status)
                channel.shutdown_write()
                channel.close()
                while transport.is_active() and not stop.wait(0.02):
                    pass
                return
        except Exception as error:  # noqa: BLE001 - report fixture-thread failures to the probe
            if not stop.is_set():
                state["errors"].append(f"{type(error).__name__}: {error}")

    thread = threading.Thread(target=run, name="jumpstarter-ssh-fixture", daemon=True)
    thread.start()
    try:
        yield address, state
    finally:
        stop.set()
        listener.close()
        for transport in transports:
            transport.close()
        thread.join(timeout=5)
        if thread.is_alive():
            raise RuntimeError("SSH fixture thread did not stop")


def worker(args):  # noqa: C901
    import paramiko
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from jumpstarter_driver_network.driver import TcpNetwork
    from jumpstarter_driver_ssh.client import SSHCommandRunOptions
    from jumpstarter_driver_ssh.driver import SSHWrapper

    from jumpstarter.common.utils import serve

    # Confine the stock client's NamedTemporaryFile identity to this task only.
    tempfile.tempdir = str(args.workdir)
    private_key = (
        Ed25519PrivateKey.generate()
        .private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.OpenSSH,
            serialization.NoEncryption(),
        )
        .decode("ascii")
    )
    public_key = paramiko.Ed25519Key.from_private_key(io.StringIO(private_key))
    config = args.workdir / "empty-ssh-config"
    config.write_text("", encoding="ascii")
    global_hosts = args.workdir / "empty-global-known-hosts"
    global_hosts.write_text("", encoding="ascii")
    expected_exit = 37 if args.probe == "nonzero_exit" else 0
    command = ["fixture-command"]
    if args.probe == "remote_command_quoting":
        command = ["printf", "'%s\\n'", "'argument with spaces'", "'café λ'"]
    options = [
        "-F",
        str(config),
        "-o",
        "BatchMode=yes",
        "-o",
        "IdentitiesOnly=yes",
        "-o",
        "IdentityAgent=none",
        "-o",
        "PreferredAuthentications=publickey",
        "-o",
        "ConnectTimeout=5",
        "-o",
        "ConnectionAttempts=1",
        "-o",
        "GlobalKnownHostsFile=" + str(global_hosts),
    ]
    driver_options = {}
    defaults = "-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR"
    if args.probe == "quoted_executable":
        # Copy only the installed executable, leaving its DLL search PATH intact.
        executable_dir = args.workdir / "ssh path λ"
        executable_dir.mkdir()
        executable = executable_dir / "ssh.exe"
        shutil.copyfile(args.ssh, executable)
        driver_options["ssh_command"] = f'"{executable}" {defaults}'
    elif args.probe == "unquoted_executable":
        driver_options["ssh_command"] = f"{args.ssh} {defaults}"
    capture_text = args.probe == "default_text"
    with ssh_fixture(public_key, expected_exit) as (address, state):
        driver = SSHWrapper(
            children={"tcp": TcpNetwork(host=address[0], port=address[1])},
            default_username="fixture-user",
            ssh_identity=private_key,
            **driver_options,
        )
        with serve(driver) as client:
            result = client.run(SSHCommandRunOptions(direct=False, capture_as_text=capture_text), options + command)
        expected_output = PAYLOAD.decode() if capture_text else PAYLOAD
        expected_command = " ".join(command)
        failures = []
        if result.return_code != expected_exit:
            failures.append(f"OpenSSH exited {result.return_code}; expected {expected_exit}")
        if result.stdout != expected_output:
            failures.append("Captured output differs from the UTF-8 fixture payload")
        if state["commands"] != [expected_command]:
            failures.append("SSH command text was not preserved")
        if not state["authentication_complete"]:
            failures.append("Ephemeral public-key authentication did not succeed")
        if state["errors"]:
            failures.extend(state["errors"])
    leftovers = [p.name for p in args.workdir.rglob("*_ssh_key")]
    if leftovers:
        failures.append("Temporary identity file was not removed")
    private_directories = [p.name for p in args.workdir.glob("jmp-*")]
    if private_directories:
        failures.append("Private identity directory was not removed")
    return {
        "probe": args.probe,
        "status": "failed" if failures else "passed",
        "failures": failures,
        "ssh_exit_code": result.return_code,
        "stdout": result.stdout if isinstance(result.stdout, str) else result.stdout.decode("utf-8", errors="replace"),
        "stderr": result.stderr if isinstance(result.stderr, str) else result.stderr.decode("utf-8", errors="replace"),
        "received_commands": state["commands"],
        "public_key_authentication": state["authentication_complete"],
        "temporary_identity_removed": not leftovers,
        "private_directory_removed": not private_directories,
        "user_known_hosts_option": "/dev/null",
    }


def run_probe(args, name):
    with tempfile.TemporaryDirectory(prefix="js-") as workdir:
        env = os.environ.copy()
        env["PATH"] = str(args.ssh.parent) + os.pathsep + env.get("PATH", "")
        for key in list(env):
            if key.startswith(("JMP_", "JUMPSTARTER_")):
                env.pop(key)
        command = [
            sys.executable,
            str(Path(__file__).resolve()),
            "--worker",
            "--probe",
            name,
            "--ssh",
            str(args.ssh),
            "--workdir",
            workdir,
        ]
        started = time.monotonic()
        process = subprocess.Popen(
            command,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
        )
        try:
            stdout, stderr = process.communicate(timeout=args.timeout)
        except subprocess.TimeoutExpired:
            cleanup_error = None
            try:
                subprocess.run(
                    ["taskkill", "/PID", str(process.pid), "/T", "/F"], capture_output=True, check=False, timeout=10
                )
            except (OSError, subprocess.SubprocessError) as error:
                cleanup_error = str(error)
            finally:
                if process.poll() is None:
                    process.kill()
                stdout, stderr = process.communicate(timeout=10)
            return {
                "probe": name,
                "status": "failed",
                "failures": ["Probe deadline exceeded"],
                "cleanup_error": cleanup_error,
                "stderr": stderr[-4000:],
            }
        records = [line[len(RESULT_PREFIX) :] for line in stdout.splitlines() if line.startswith(RESULT_PREFIX)]
        if len(records) != 1:
            return {
                "probe": name,
                "status": "failed",
                "failures": ["Worker returned no unique result"],
                "return_code": process.returncode,
                "stderr": stderr[-4000:],
            }
        result = json.loads(records[0])
        expected_code = 0 if result.get("status") == "passed" else 1
        if process.returncode != expected_code or result.get("probe") != name:
            result = {
                "probe": name,
                "status": "failed",
                "failures": ["Invalid worker status envelope"],
                "return_code": process.returncode,
                "worker_result": result,
            }
        result["duration_seconds"] = round(time.monotonic() - started, 3)
        return result


def main():
    args = arguments()
    if sys.platform != "win32":
        raise SystemExit("This runner qualifies the native Windows OpenSSH client")
    args.ssh = args.ssh or (Path(shutil.which("ssh")) if shutil.which("ssh") else None)
    if args.ssh is None or not args.ssh.is_file():
        raise SystemExit("Native ssh.exe was not found")
    args.ssh = args.ssh.resolve()
    if args.worker:
        try:
            result = worker(args)
        except Exception as error:  # noqa: BLE001 - every worker failure must reach the parent report
            result = {"probe": args.probe, "status": "failed", "failures": [f"{type(error).__name__}: {error}"]}
        print(RESULT_PREFIX + json.dumps(result, ensure_ascii=True))
        return 0 if result["status"] == "passed" else 1
    ssh_version = subprocess.run([str(args.ssh), "-V"], capture_output=True, text=True, timeout=5, check=True)
    results = [run_probe(args, name) for name in ([args.probe] if args.probe else PROBES)]
    document = {
        "environment": {
            "platform": platform.platform(),
            "python": sys.version,
            "ssh": str(args.ssh),
            "ssh_version": (ssh_version.stdout + ssh_version.stderr).strip(),
        },
        "scope": (
            "Real native OpenSSH, generated key authentication, stock SSHWrapperClient and TCP forwarding; "
            "local Paramiko simulator never executes commands"
        ),
        "probes": results,
    }
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(document, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(document, indent=2, ensure_ascii=True))
    return 0 if all(result["status"] == "passed" for result in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
