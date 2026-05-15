from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from urllib.parse import urlparse

import click
from jumpstarter_driver_composite.client import CompositeClient
from jumpstarter_driver_network.adapters import TcpPortforwardAdapter

from jumpstarter.client.core import DriverMethodNotImplemented
from jumpstarter.client.decorators import driver_click_command

SUBPROCESS_TIMEOUT = 120
MOUNT_POLL_INTERVAL = 0.5
MOUNT_POLL_TIMEOUT = 10


@dataclass(kw_only=True)
class SSHMountClient(CompositeClient):

    def cli(self) -> click.Command:
        @driver_click_command(self)
        @click.argument("mountpoint", type=click.Path())
        @click.option("--umount", "-u", is_flag=True, help="Unmount instead of mount")
        @click.option("--remote-path", "-r", default="/", help="Remote path to mount (default: /)")
        @click.option("--direct", is_flag=True, help="Use direct TCP address")
        @click.option("--lazy", "-l", is_flag=True, help="Lazy unmount (detach filesystem now, clean up later)")
        @click.option("--foreground", is_flag=True, help="Block on sshfs in foreground without spawning a subshell")
        @click.option("--extra-args", "-o", multiple=True, help="Extra sshfs -o options (e.g. -o reconnect)")
        def mount(mountpoint, umount, remote_path, direct, lazy, foreground, extra_args):
            """Mount or unmount remote filesystem via sshfs"""
            if umount:
                self.umount(mountpoint, lazy=lazy)
            else:
                self.mount(
                    mountpoint,
                    remote_path=remote_path,
                    direct=direct,
                    foreground=foreground,
                    extra_args=list(extra_args),
                )

        return mount

    @property
    def identity(self) -> str | None:
        return self.ssh.identity

    @property
    def username(self) -> str:
        return self.ssh.username

    def _resolve_host_port(self, direct: bool) -> tuple[str, int, bool]:
        if direct:
            try:
                address = self.ssh.tcp.address()
                parsed = urlparse(address)
                host = parsed.hostname
                port = parsed.port
                if not host or not port:
                    raise ValueError(f"Invalid address format: {address}")
                self.logger.debug("Using direct TCP: %s:%s", host, port)
                return host, port, False
            except (DriverMethodNotImplemented, ValueError) as e:
                self.logger.error("Direct connection failed (%s), falling back to port forwarding", e)
        return "", 0, True

    def mount(
        self,
        mountpoint: str,
        *,
        remote_path: str = "/",
        direct: bool = False,
        foreground: bool = False,
        extra_args: list[str] | None = None,
    ) -> None:
        """Mount remote filesystem locally via sshfs."""
        if not self._find_executable("sshfs"):
            raise click.ClickException(
                "sshfs is not installed. Please install it:\n"
                "  Fedora/RHEL: sudo dnf install fuse-sshfs\n"
                "  Debian/Ubuntu: sudo apt-get install sshfs\n"
                "  macOS: Install macFUSE and SSHFS from https://macfuse.github.io/\n"
                "         Note: macOS kernel extensions require special handling;\n"
                "         read the install documentation carefully."
            )

        mountpoint = os.path.realpath(mountpoint)
        os.makedirs(mountpoint, exist_ok=True)

        host, port, use_portforward = self._resolve_host_port(direct)

        if use_portforward:
            with TcpPortforwardAdapter(client=self.ssh.tcp) as (host, port):
                self._run_sshfs(host, port, mountpoint, remote_path, extra_args,
                                foreground=foreground)
        else:
            self._run_sshfs(host, port, mountpoint, remote_path, extra_args,
                            foreground=foreground)

    def _run_sshfs(
        self,
        host: str,
        port: int,
        mountpoint: str,
        remote_path: str,
        extra_args: list[str] | None,
        *,
        foreground: bool,
    ) -> None:
        identity_file = self._create_temp_identity_file()
        sshfs_proc: subprocess.Popen[bytes] | None = None

        try:
            sshfs_args = self._build_sshfs_args(
                host, port, mountpoint, remote_path, identity_file, extra_args,
            )
            sshfs_args.append("-f")
            sshfs_proc = self._start_sshfs_with_fallback(sshfs_args, mountpoint)

            user_prefix = f"{self.username}@" if self.username else ""
            host_spec = f"[{host}]" if ":" in host else host
            click.echo(f"Mounted {user_prefix}{host_spec}:{remote_path} on {mountpoint}")

            if foreground:
                click.echo("Press Ctrl+C to unmount and exit.")
                try:
                    sshfs_proc.wait()
                except KeyboardInterrupt:
                    click.echo("\nUnmounting...")
            else:
                click.echo("Type 'exit' to unmount and return.")
                self._run_subshell(mountpoint, remote_path)
        finally:
            if sshfs_proc is not None:
                self._terminate_proc(sshfs_proc)

            self._force_umount(mountpoint)
            if os.path.ismount(mountpoint):
                self.logger.warning("Mountpoint %s may still be mounted after cleanup", mountpoint)
            else:
                click.echo(f"Unmounted {mountpoint}")
            self._cleanup_identity_file(identity_file)

    @staticmethod
    def _terminate_proc(proc: subprocess.Popen[bytes]) -> None:
        if proc.poll() is not None:
            return
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()

    def _start_sshfs_with_fallback(
        self, sshfs_args: list[str], mountpoint: str,
    ) -> subprocess.Popen[bytes]:
        test_args = [a for a in sshfs_args if a != "-f"]
        result = subprocess.run(test_args, capture_output=True, text=True, timeout=SUBPROCESS_TIMEOUT)

        if result.returncode != 0 and "allow_other" in result.stderr:
            self.logger.debug("Retrying sshfs without allow_other option")
            sshfs_args = self._remove_allow_other(sshfs_args)
            test_args = [a for a in sshfs_args if a != "-f"]
            result = subprocess.run(test_args, capture_output=True, text=True, timeout=SUBPROCESS_TIMEOUT)

        if result.returncode != 0:
            stderr = result.stderr.strip()
            raise click.ClickException(
                f"sshfs mount failed (exit code {result.returncode}): {stderr}"
            )

        self._force_umount(mountpoint)
        if os.path.ismount(mountpoint):
            raise click.ClickException(
                f"Failed to unmount test mount at {mountpoint}; cannot proceed"
            )

        proc = subprocess.Popen(
            sshfs_args,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        try:
            try:
                proc.wait(timeout=1)
                raise click.ClickException(
                    f"sshfs mount failed immediately (exit code {proc.returncode})"
                )
            except subprocess.TimeoutExpired:
                pass

            deadline = time.monotonic() + MOUNT_POLL_TIMEOUT
            while time.monotonic() < deadline:
                if os.path.ismount(mountpoint):
                    return proc
                time.sleep(MOUNT_POLL_INTERVAL)

            self._terminate_proc(proc)
            raise click.ClickException(
                f"sshfs started but {mountpoint} is not mounted after {MOUNT_POLL_TIMEOUT}s"
            )
        except BaseException:
            self._terminate_proc(proc)
            raise

    def _remove_allow_other(self, sshfs_args: list[str]) -> list[str]:
        filtered: list[str] = []
        skip_next = False
        for i, arg in enumerate(sshfs_args):
            if skip_next:
                skip_next = False
                continue
            if arg == "-o" and i + 1 < len(sshfs_args):
                parts = [p for p in sshfs_args[i + 1].split(",") if p != "allow_other"]
                if parts:
                    filtered.append("-o")
                    filtered.append(",".join(parts))
                skip_next = True
                continue
            filtered.append(arg)
        return filtered

    def _run_subshell(self, mountpoint: str, remote_path: str) -> None:
        shell = os.environ.get("SHELL", "/bin/sh")
        shell_name = os.path.basename(shell)
        env = os.environ.copy()

        mount_tag = "(mount)"
        try:
            if shell_name.endswith("bash"):
                ps1 = env.get("PS1", r"\$ ")
                if "➤" in ps1:
                    ps1 = ps1.replace("➤", f"{mount_tag}➤")
                else:
                    ps1 = f"[sshfs:{remote_path}] {ps1}"
                env["PS1"] = ps1
                subprocess.run(
                    [shell, "--norc", "--noprofile", "-i"],
                    env=env,
                )
            elif shell_name == "fish":
                fish_fn = (
                    "function fish_prompt; "
                    "set_color grey; "
                    'printf "%s" (basename $PWD); '
                    "set_color yellow; "
                    'printf "⚡"; '
                    "set_color white; "
                    f'printf "{mount_tag}"; '
                    "set_color yellow; "
                    'printf "➤ "; '
                    "set_color normal; "
                    "end"
                )
                subprocess.run([shell, "--init-command", fish_fn], env=env)
            elif shell_name == "zsh":
                ps1 = env.get("PS1", "%# ")
                if "➤" in ps1:
                    ps1 = ps1.replace("➤", f"{mount_tag}➤")
                else:
                    ps1 = f"[sshfs:{remote_path}] {ps1}"
                env["PS1"] = ps1
                subprocess.run([shell, "--no-rcs", "-i"], env=env)
            else:
                subprocess.run([shell, "-i"], env=env)
        except FileNotFoundError as err:
            raise click.ClickException(
                f"Shell '{shell}' not found. Set the SHELL environment variable to a valid shell."
            ) from err

    def _build_sshfs_args(
        self,
        host: str,
        port: int,
        mountpoint: str,
        remote_path: str,
        identity_file: str | None,
        extra_args: list[str] | None,
    ) -> list[str]:
        default_username = self.username
        user_prefix = f"{default_username}@" if default_username else ""
        host_spec = f"[{host}]" if ":" in host else host
        remote_spec = f"{user_prefix}{host_spec}:{remote_path}"

        sshfs_args = ["sshfs", remote_spec, mountpoint]

        if port and port != 22:
            sshfs_args.extend(["-p", str(port)])

        if extra_args:
            for arg in extra_args:
                sshfs_args.extend(["-o", arg])

        ssh_opts = [
            "StrictHostKeyChecking=no",
            "UserKnownHostsFile=/dev/null",
            "LogLevel=ERROR",
        ]

        if identity_file:
            ssh_opts.append(f"IdentityFile={identity_file}")

        for opt in ssh_opts:
            sshfs_args.extend(["-o", opt])

        return sshfs_args

    def _create_temp_identity_file(self) -> str | None:
        ssh_identity = self.identity
        if not ssh_identity:
            return None

        fd = None
        temp_path = None
        try:
            fd, temp_path = tempfile.mkstemp(suffix='_ssh_key')
            os.fchmod(fd, 0o600)
            os.write(fd, ssh_identity.encode())
            os.close(fd)
            fd = None
            return temp_path
        except Exception as e:
            self.logger.error("Failed to create temporary identity file: %s", e)
            if fd is not None:
                try:
                    os.close(fd)
                except Exception:
                    pass
            if temp_path:
                try:
                    os.unlink(temp_path)
                except Exception:
                    pass
            raise

    def _cleanup_identity_file(self, identity_file: str | None) -> None:
        if identity_file:
            try:
                os.unlink(identity_file)
            except Exception as e:
                self.logger.warning("Failed to clean up identity file %s: %s", identity_file, e)

    def umount(self, mountpoint: str, *, lazy: bool = False) -> None:
        """Unmount an sshfs filesystem (fallback for orphaned mounts)."""
        mountpoint = os.path.realpath(mountpoint)
        cmd = self._build_umount_cmd(mountpoint, lazy=lazy)

        self.logger.debug("Running unmount command: %s", cmd)
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=SUBPROCESS_TIMEOUT)

        if result.returncode != 0:
            stderr = result.stderr.strip()
            raise click.ClickException(f"Unmount failed (exit code {result.returncode}): {stderr}")

        click.echo(f"Unmounted {mountpoint}")

    def _force_umount(self, mountpoint: str) -> None:
        cmd = self._build_umount_cmd(mountpoint, lazy=False)
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=SUBPROCESS_TIMEOUT)
            if result.returncode != 0:
                self.logger.debug("Force umount of %s returned %d: %s",
                                  mountpoint, result.returncode, result.stderr.strip())
        except Exception as e:
            self.logger.debug("Force umount of %s failed: %s", mountpoint, e)

    def _build_umount_cmd(self, mountpoint: str, *, lazy: bool = False) -> list[str]:
        fusermount = self._find_executable("fusermount3") or self._find_executable("fusermount")
        if fusermount:
            cmd = [fusermount, "-u"]
            if lazy:
                cmd.append("-z")
        else:
            cmd = ["umount"]
            if lazy:
                if sys.platform == "darwin":
                    cmd.append("-f")
                else:
                    cmd.append("-l")
        cmd.append(mountpoint)
        return cmd

    @staticmethod
    def _find_executable(name: str) -> str | None:
        return shutil.which(name)
