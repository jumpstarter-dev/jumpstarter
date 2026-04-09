from __future__ import annotations

import os
import subprocess
import tempfile
import threading
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

import click
from jumpstarter_driver_composite.client import CompositeClient
from jumpstarter_driver_network.adapters import TcpPortforwardAdapter

from jumpstarter.client.core import DriverMethodNotImplemented
from jumpstarter.client.decorators import driver_click_command

# Timeout in seconds for subprocess calls (sshfs mount/umount)
SUBPROCESS_TIMEOUT = 120


@dataclass
class _MountInfo:
    """Tracks state associated with an active sshfs mount."""
    mountpoint: str
    identity_file: str | None = None
    port_forward: Any | None = None
    port_forward_thread: threading.Thread | None = None


@dataclass(kw_only=True)
class SSHMountClient(CompositeClient):
    _active_mounts: dict[str, _MountInfo] = field(default_factory=dict, init=False, repr=False)

    def cli(self):
        @driver_click_command(self)
        @click.argument("mountpoint", type=click.Path())
        @click.option("--umount", "-u", is_flag=True, help="Unmount instead of mount")
        @click.option("--remote-path", "-r", default="/", help="Remote path to mount (default: /)")
        @click.option("--direct", is_flag=True, help="Use direct TCP address")
        @click.option("--lazy", "-l", is_flag=True, help="Lazy unmount (detach filesystem now, clean up later)")
        @click.option("--extra-args", "-o", multiple=True, help="Extra arguments to pass to sshfs")
        def mount(mountpoint, umount, remote_path, direct, lazy, extra_args):
            """Mount or unmount remote filesystem via sshfs"""
            if umount:
                self.umount(mountpoint, lazy=lazy)
            else:
                self.mount(mountpoint, remote_path=remote_path, direct=direct, extra_args=list(extra_args))

        return mount

    @property
    def identity(self) -> str | None:
        return self.ssh.identity

    @property
    def username(self) -> str:
        return self.ssh.username

    def mount(self, mountpoint, *, remote_path="/", direct=False, extra_args=None):
        """Mount remote filesystem locally via sshfs"""
        # Verify sshfs is available
        sshfs_path = self._find_executable("sshfs")
        if not sshfs_path:
            raise click.ClickException(
                "sshfs is not installed. Please install it:\n"
                "  Fedora/RHEL: sudo dnf install fuse-sshfs\n"
                "  Debian/Ubuntu: sudo apt-get install sshfs\n"
                "  macOS: Install macFUSE from https://macfuse.github.io/ and then install\n"
                "         sshfs from source, as Homebrew has removed sshfs support."
            )

        # Resolve to absolute path for consistent tracking
        mountpoint = os.path.realpath(mountpoint)

        # Create mountpoint directory if it doesn't exist
        os.makedirs(mountpoint, exist_ok=True)

        if direct:
            try:
                address = self.ssh.tcp.address()
                parsed = urlparse(address)
                host = parsed.hostname
                port = parsed.port
                if not host or not port:
                    raise ValueError(f"Invalid address format: {address}")
                self.logger.debug("Using direct TCP connection for sshfs - host: %s, port: %s", host, port)
                self._run_sshfs(host, port, mountpoint, remote_path, extra_args, port_forward=None)
            except (DriverMethodNotImplemented, ValueError) as e:
                self.logger.error(
                    "Direct address connection failed (%s), falling back to port forwarding", e
                )
                self.mount(mountpoint, remote_path=remote_path, direct=False, extra_args=extra_args)
        else:
            self.logger.debug("Using SSH port forwarding for sshfs connection")
            # Create port forward adapter and keep it alive for the duration of the mount.
            # We enter the context manager manually and only exit it on umount.
            adapter = TcpPortforwardAdapter(client=self.ssh.tcp)
            host, port = adapter.__enter__()
            self.logger.debug("SSH port forward established - host: %s, port: %s", host, port)
            try:
                self._run_sshfs(host, port, mountpoint, remote_path, extra_args, port_forward=adapter)
            except Exception:
                # If sshfs failed, tear down the port forward immediately
                adapter.__exit__(None, None, None)
                raise

    def _run_sshfs(self, host, port, mountpoint, remote_path, extra_args, *, port_forward):
        identity_file = self._create_temp_identity_file()

        try:
            sshfs_args = self._build_sshfs_args(host, port, mountpoint, remote_path, identity_file, extra_args)
            self.logger.debug("Running sshfs command: %s", sshfs_args)

            result = subprocess.run(sshfs_args, capture_output=True, text=True, timeout=SUBPROCESS_TIMEOUT)
            result = self._retry_sshfs_without_allow_other(result, sshfs_args)

            if result.returncode != 0:
                stderr = result.stderr.strip()
                raise click.ClickException(
                    f"sshfs mount failed (exit code {result.returncode}): {stderr}"
                )

            # Track this mount so we can clean up on umount
            self._active_mounts[mountpoint] = _MountInfo(
                mountpoint=mountpoint,
                identity_file=identity_file,
                port_forward=port_forward,
            )

            default_username = self.username
            user_prefix = f"{default_username}@" if default_username else ""
            remote_spec = f"{user_prefix}{host}:{remote_path}"
            click.echo(f"Mounted {remote_spec} on {mountpoint}")
            click.echo(f"To unmount: j mount --umount {mountpoint}")
        except click.ClickException:
            # Clean up identity file on failure
            self._cleanup_identity_file(identity_file)
            raise
        except Exception as e:
            self._cleanup_identity_file(identity_file)
            raise click.ClickException(f"Failed to mount: {e}") from e

    def _build_sshfs_args(self, host, port, mountpoint, remote_path, identity_file, extra_args):
        default_username = self.username
        user_prefix = f"{default_username}@" if default_username else ""
        remote_spec = f"{user_prefix}{host}:{remote_path}"

        sshfs_args = ["sshfs", remote_spec, mountpoint]

        ssh_opts = [
            "StrictHostKeyChecking=no",
            "UserKnownHostsFile=/dev/null",
            "LogLevel=ERROR",
        ]

        if port and port != 22:
            sshfs_args.extend(["-p", str(port)])

        if identity_file:
            ssh_opts.append(f"IdentityFile={identity_file}")

        ssh_opts.append("allow_other")

        for opt in ssh_opts:
            sshfs_args.extend(["-o", opt])

        if extra_args:
            sshfs_args.extend(extra_args)

        return sshfs_args

    def _retry_sshfs_without_allow_other(self, result, sshfs_args):
        """Retry sshfs without allow_other if it failed due to that option"""
        if result.returncode != 0 and "allow_other" in result.stderr:
            self.logger.debug("Retrying sshfs without allow_other option")
            # Remove both the "-o" flag and the "allow_other" value together
            filtered = []
            skip_next = False
            for i, arg in enumerate(sshfs_args):
                if skip_next:
                    skip_next = False
                    continue
                if arg == "-o" and i + 1 < len(sshfs_args) and sshfs_args[i + 1] == "allow_other":
                    skip_next = True
                    continue
                filtered.append(arg)
            return subprocess.run(filtered, capture_output=True, text=True, timeout=SUBPROCESS_TIMEOUT)
        return result

    def _create_temp_identity_file(self):
        """Create a temporary file with the SSH identity key, if configured."""
        ssh_identity = self.identity
        if not ssh_identity:
            return None

        temp_file = None
        try:
            temp_file = tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='_ssh_key')
            temp_file.write(ssh_identity)
            temp_file.close()
            os.chmod(temp_file.name, 0o600)
            self.logger.debug("Created temporary identity file: %s", temp_file.name)
            return temp_file.name
        except Exception as e:
            self.logger.error("Failed to create temporary identity file: %s", e)
            if temp_file:
                try:
                    os.unlink(temp_file.name)
                except Exception:
                    pass
            raise

    def _cleanup_identity_file(self, identity_file):
        """Remove a temporary identity file if it exists."""
        if identity_file:
            try:
                os.unlink(identity_file)
                self.logger.debug("Cleaned up temporary identity file: %s", identity_file)
            except Exception as e:
                self.logger.warning("Failed to clean up identity file %s: %s", identity_file, e)

    def umount(self, mountpoint=None, *, lazy=False):
        """Unmount a previously mounted sshfs filesystem.

        If mountpoint is None, unmounts all active mounts from this session.
        """
        if mountpoint is None:
            # Unmount everything from this session
            if not self._active_mounts:
                click.echo("No active mounts to unmount.")
                return
            # Copy keys to avoid mutation during iteration
            for mp in list(self._active_mounts.keys()):
                self.umount(mp, lazy=lazy)
            return

        mountpoint = os.path.realpath(mountpoint)

        # Try fusermount first (Linux), fall back to umount (macOS)
        fusermount = self._find_executable("fusermount3") or self._find_executable("fusermount")
        if fusermount:
            cmd = [fusermount, "-u"]
            if lazy:
                cmd.append("-z")
            cmd.append(mountpoint)
        else:
            cmd = ["umount"]
            if lazy:
                cmd.append("-l")
            cmd.append(mountpoint)

        self.logger.debug("Running unmount command: %s", cmd)
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=SUBPROCESS_TIMEOUT)

        if result.returncode != 0:
            stderr = result.stderr.strip()
            raise click.ClickException(f"Unmount failed (exit code {result.returncode}): {stderr}")

        # Clean up tracked resources for this mount
        mount_info = self._active_mounts.pop(mountpoint, None)
        if mount_info:
            self._cleanup_identity_file(mount_info.identity_file)
            if mount_info.port_forward:
                try:
                    mount_info.port_forward.__exit__(None, None, None)
                    self.logger.debug("Closed port forward for %s", mountpoint)
                except Exception as e:
                    self.logger.warning("Failed to close port forward for %s: %s", mountpoint, e)

        click.echo(f"Unmounted {mountpoint}")

    @staticmethod
    def _find_executable(name):
        """Find an executable in PATH, return full path or None"""
        import shutil
        return shutil.which(name)
