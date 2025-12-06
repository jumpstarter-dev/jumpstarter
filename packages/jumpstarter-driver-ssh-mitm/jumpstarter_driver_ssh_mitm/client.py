"""
SSH MITM Client - Secure SSH access without exposing private keys.

This client connects to the SSHMITM driver which keeps SSH private keys
secure on the exporter. Commands can be executed via gRPC or by tunneling
through a local SSH port that is forwarded to the driver's MITM proxy.

Usage:
    j ssh_mitm <cmd>            # Execute command via gRPC
    j ssh_mitm shell            # Native SSH via port forwarding
    j ssh_mitm shell --repl     # Interactive gRPC REPL shell
    j ssh_mitm forward -p 2222  # Port forward for ssh/scp/rsync
"""

import os
import shlex
import shutil
import subprocess
import textwrap
import time
import uuid
from dataclasses import dataclass
from difflib import get_close_matches

import click
from jumpstarter_driver_network.adapters import TcpPortforwardAdapter

from jumpstarter.client import DriverClient


class DefaultCommandGroup(click.Group):
    """
    Click group that falls back to a default subcommand when none matches,
    but only if the user didn't intend to invoke an actual subcommand.
    """

    def __init__(self, *args, default_command: str | None = None, **kwargs):
        self.default_command = default_command
        super().__init__(*args, **kwargs)

    def resolve_command(self, ctx, args):
        try:
            return super().resolve_command(ctx, args)
        except click.UsageError as original_error:
            if self.default_command is None or not args:
                raise

            # If the first argument is close to an existing subcommand,
            # treat it as a typo and re-raise the original error.
            first_token = args[0]
            subcommand_names = list(self.commands.keys())
            close_matches = get_close_matches(first_token, subcommand_names, n=1, cutoff=0.8)
            if close_matches:
                raise original_error

            cmd = self.get_command(ctx, self.default_command)
            return cmd.name, cmd, args


@dataclass
class SSHMITMCommandRunResult:
    """Result of executing a command via SSH MITM."""

    return_code: int
    stdout: str
    stderr: str


@dataclass(kw_only=True)
class SSHMITMClient(DriverClient):
    """
    Client for SSH MITM proxy driver.

    Provides secure SSH access where the private key never leaves the exporter.
    Commands are executed via gRPC - the driver runs SSH on behalf of the client.
    """

    def cli(self):  # noqa: C901
        """Create CLI command for 'j ssh_mitm'."""
        client = self

        @click.group(
            "ssh",
            cls=DefaultCommandGroup,
            default_command="run",
            invoke_without_command=True,
            context_settings={"ignore_unknown_options": True, "allow_extra_args": True},
            help=client.description or "SSH MITM - secure SSH to DUT",
        )
        def ssh_cmd():
            """SSH MITM group."""
            pass

        @ssh_cmd.command(
            "run",
            context_settings={"ignore_unknown_options": True, "allow_extra_args": True},
        )
        @click.argument("args", nargs=-1, type=click.UNPROCESSED)
        @click.pass_context
        def run(ctx, args):
            """Default command execution via gRPC."""
            if not args:
                click.echo("Usage:")
                click.echo("  j ssh_mitm <command> [args...]")
                click.echo("  j ssh_mitm shell [ssh options/remote command]")
                click.echo("  j ssh_mitm forward [--port PORT]")
                click.echo("\nExamples:")
                click.echo("  j ssh_mitm whoami")
                click.echo("  j ssh_mitm ls -la /tmp")
                click.echo("  j ssh_mitm shell")
                click.echo("  j ssh_mitm forward -p 2222")
                return

            result = client.execute(args)

            if result.stdout:
                click.echo(result.stdout, nl=False)
            if result.stderr:
                click.echo(result.stderr, nl=False, err=True)

            if result.return_code != 0:
                ctx.exit(result.return_code)

        @ssh_cmd.command("shell")
        @click.option(
            "--repl",
            is_flag=True,
            help="Use simple gRPC REPL instead of launching native ssh",
        )
        @click.argument("ssh_args", nargs=-1, type=click.UNPROCESSED)
        @click.pass_context
        def shell(ctx, repl, ssh_args):
            """
            Launch an SSH session through the MITM proxy.

            By default, spawns the system 'ssh' binary via port forwarding.
            Use --repl for the lightweight gRPC REPL shell.
            """
            if repl:
                client._run_shell()
            else:
                exit_code = client._launch_native_ssh(ssh_args)
                if exit_code != 0:
                    ctx.exit(exit_code)

        @ssh_cmd.command("forward")
        @click.option(
            "--host",
            "local_host",
            default="127.0.0.1",
            show_default=True,
            help="Local interface to bind",
        )
        @click.option(
            "-p",
            "--port",
            "local_port",
            type=int,
            default=0,
            help="Local port (0 = auto)",
            show_default=True,
        )
        def forward(local_host, local_port):
            """
            Expose the MITM proxy as a local TCP port for native SSH/scp/rsync.

            Example:
                j ssh_mitm forward -p 2222
                ssh -p 2222 localhost
            """
            client._start_forward(local_host, local_port)

        return ssh_cmd

    def _ensure_ssh_binary(self) -> str:
        ssh_path = shutil.which("ssh")
        if not ssh_path:
            raise click.ClickException("'ssh' binary not found in PATH")
        return ssh_path

    def _launch_native_ssh(self, ssh_args: tuple[str, ...]) -> int:
        username = self.call("get_default_username") or os.environ.get("USER", "root")
        ssh_binary = self._ensure_ssh_binary()

        with TcpPortforwardAdapter(client=self, method="connect") as (host, port):
            ssh_command = [
                ssh_binary,
                "-p",
                str(port),
                "-o",
                "StrictHostKeyChecking=no",
                "-o",
                "UserKnownHostsFile=/dev/null",
                f"{username}@{host}",
            ]
            if ssh_args:
                ssh_command.extend(ssh_args)

            self.logger.debug("Launching native SSH: %s", shlex.join(ssh_command))
            return subprocess.call(ssh_command)

    def _run_shell(self):  # noqa: C901
        """Run interactive shell via gRPC commands."""
        username = self.call("get_default_username") or "user"
        hostname = "dut"

        try:
            result = self.execute(["hostname", "-s"])
            if result.return_code == 0 and result.stdout.strip():
                hostname = result.stdout.strip()
        except Exception as e:
            self.logger.debug("Failed to get hostname: %s", e)

        click.echo(f"Connected to {hostname} via SSH MITM proxy")
        click.echo("Type 'exit' or Ctrl+D to exit")
        click.echo()

        cwd = "~"

        while True:
            try:
                prompt = click.style(f"{username}@{hostname}", fg="green", bold=True)
                prompt += click.style(":", fg="white")
                prompt += click.style(cwd, fg="blue", bold=True)
                prompt += click.style("$ ", fg="white")

                cmd = input(prompt)

                if not cmd.strip():
                    continue

                if cmd.strip() == "exit":
                    click.echo("Connection closed.")
                    break

                if cmd.strip().startswith("cd "):
                    new_dir = cmd.strip()[3:].strip()
                    result = self.execute(
                        [
                            "bash",
                            "-c",
                            f"cd {shlex.quote(cwd)} 2>/dev/null; cd {shlex.quote(new_dir)} && pwd",
                        ]
                    )
                    if result.return_code == 0 and result.stdout.strip():
                        cwd = result.stdout.strip()
                    else:
                        click.echo(f"cd: {new_dir}: No such file or directory", err=True)
                    continue

                if cmd.strip() == "cd":
                    cwd = "~"
                    continue

                # Execute command in current directory using newline-delimited heredoc to avoid interpolation
                token = f"JSSHMITM_{uuid.uuid4().hex}"
                script = (
                    textwrap.dedent(
                        f"""
                        cd {shlex.quote(cwd)} 2>/dev/null || cd ~
                        cat <<'{token}' | bash
                        {cmd}
                        {token}
                        """
                    ).strip()
                    + "\n"
                )
                result = self.execute(["bash", "-lc", script])

                if result.stdout:
                    click.echo(result.stdout, nl=False)
                if result.stderr:
                    click.echo(result.stderr, nl=False, err=True)

            except EOFError:
                click.echo()
                click.echo("Connection closed.")
                break
            except KeyboardInterrupt:
                click.echo("^C")
                continue

    def _start_forward(self, local_host: str, local_port: int):
        """Expose the SSH MITM server on a local TCP port."""
        click.echo("Starting local forward (Ctrl+C to stop)...")
        try:
            with TcpPortforwardAdapter(
                client=self,
                method="connect",
                local_host=local_host,
                local_port=local_port,
            ) as (bound_host, bound_port):
                click.echo(f"Local endpoint: {bound_host}:{bound_port}")
                click.echo(f"Example: ssh -p {bound_port} localhost")
                click.echo("Press Ctrl+C to stop forwarding.")
                while True:
                    time.sleep(1)
        except KeyboardInterrupt:
            click.echo("\nForward stopped.")

    def execute(self, args) -> SSHMITMCommandRunResult:
        """
        Execute command on DUT via gRPC.

        The command is run on the exporter using the stored SSH key,
        then results are returned.
        """
        return_code, stdout, stderr = self.call("execute_command", *args)

        return SSHMITMCommandRunResult(
            return_code=return_code,
            stdout=stdout,
            stderr=stderr,
        )

    def run(self, args) -> SSHMITMCommandRunResult:
        """Alias for execute()."""
        return self.execute(args)
