import os
import subprocess
import tempfile
from dataclasses import dataclass
from urllib.parse import urlparse

import click
from jumpstarter_driver_composite.client import CompositeClient
from jumpstarter_driver_network.adapters import TcpPortforwardAdapter

from jumpstarter.client.core import DriverMethodNotImplemented
from jumpstarter.client.decorators import driver_click_group


@dataclass(kw_only=True)
class SSHMountClient(CompositeClient):
    """
    Client interface for SSHMount driver

    This client provides mount/umount commands for remote filesystem
    mounting via sshfs.
    """

    def cli(self):
        @driver_click_group(self)
        def ssh_mount():
            """SSHFS mount/umount commands for remote filesystems"""
            pass

        @ssh_mount.command("mount")
        @click.argument("mountpoint", type=click.Path())
        @click.option("--remote-path", "-r", default="/", help="Remote path to mount (default: /)")
        @click.option("--direct", is_flag=True, help="Use direct TCP address")
        @click.option("--extra-args", "-o", multiple=True, help="Extra arguments to pass to sshfs")
        def mount_cmd(mountpoint, remote_path, direct, extra_args):
            """Mount remote filesystem locally via sshfs"""
            self.mount(mountpoint, remote_path=remote_path, direct=direct, extra_args=list(extra_args))

        @ssh_mount.command("umount")
        @click.argument("mountpoint", type=click.Path(exists=True))
        @click.option("--lazy", "-l", is_flag=True, help="Lazy unmount (detach filesystem now, clean up later)")
        def umount_cmd(mountpoint, lazy):
            """Unmount a previously mounted sshfs filesystem"""
            self.umount(mountpoint, lazy=lazy)

        return ssh_mount

    @property
    def identity(self) -> str | None:
        """
        Get the SSH identity (private key) as a string from the SSH driver.

        Returns:
            The SSH identity key content, or None if not configured.
        """
        return self.ssh.identity

    @property
    def username(self) -> str:
        """Get the default SSH username from the SSH driver"""
        return self.ssh.username

    def mount(self, mountpoint, *, remote_path="/", direct=False, extra_args=None):
        """Mount remote filesystem locally via sshfs

        Args:
            mountpoint: Local directory to mount the remote filesystem on
            remote_path: Remote path to mount (default: /)
            direct: If True, connect directly to the host's TCP address
            extra_args: Extra arguments to pass to sshfs
        """
        # Verify sshfs is available
        sshfs_path = self._find_executable("sshfs")
        if not sshfs_path:
            raise click.ClickException(
                "sshfs is not installed. Please install it:\n"
                "  Fedora/RHEL: sudo dnf install fuse-sshfs\n"
                "  Debian/Ubuntu: sudo apt-get install sshfs\n"
                "  macOS: brew install macfuse && brew install sshfs"
            )

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
                self._run_sshfs(host, port, mountpoint, remote_path, extra_args)
            except (DriverMethodNotImplemented, ValueError) as e:
                self.logger.error(
                    "Direct address connection failed (%s), falling back to port forwarding", e
                )
                self.mount(mountpoint, remote_path=remote_path, direct=False, extra_args=extra_args)
        else:
            self.logger.debug("Using SSH port forwarding for sshfs connection")
            with TcpPortforwardAdapter(client=self.ssh.tcp) as addr:
                host, port = addr
                self.logger.debug("SSH port forward established - host: %s, port: %s", host, port)
                self._run_sshfs(host, port, mountpoint, remote_path, extra_args)

    def _run_sshfs(self, host, port, mountpoint, remote_path, extra_args=None):
        """Run sshfs to mount remote filesystem"""
        identity_file = self._create_temp_identity_file()

        try:
            sshfs_args = self._build_sshfs_args(host, port, mountpoint, remote_path, identity_file, extra_args)
            self.logger.debug("Running sshfs command: %s", sshfs_args)

            result = subprocess.run(sshfs_args, capture_output=True, text=True)
            result = self._retry_sshfs_without_allow_other(result, sshfs_args)

            if result.returncode != 0:
                stderr = result.stderr.strip()
                raise click.ClickException(
                    f"sshfs mount failed (exit code {result.returncode}): {stderr}"
                )

            default_username = self.username
            user_prefix = f"{default_username}@" if default_username else ""
            remote_spec = f"{user_prefix}{host}:{remote_path}"
            click.echo(f"Mounted {remote_spec} on {mountpoint}")
            click.echo(f"To unmount: j ssh-mount umount {mountpoint}")
        except click.ClickException:
            raise
        except Exception as e:
            raise click.ClickException(f"Failed to mount: {e}") from e
        finally:
            if identity_file:
                self.logger.info(
                    "Temporary SSH key file %s will persist until unmount. "
                    "It has permissions 0600.",
                    identity_file,
                )

    def _build_sshfs_args(self, host, port, mountpoint, remote_path, identity_file, extra_args):
        """Build the sshfs command arguments"""
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
            sshfs_args = [arg for arg in sshfs_args if arg != "allow_other"]
            return subprocess.run(sshfs_args, capture_output=True, text=True)
        return result

    def _create_temp_identity_file(self):
        """Create a temporary file with the SSH identity key, if configured"""
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

    def umount(self, mountpoint, *, lazy=False):
        """Unmount a previously mounted sshfs filesystem

        Args:
            mountpoint: Local mount point to unmount
            lazy: If True, use lazy unmount
        """
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
        result = subprocess.run(cmd, capture_output=True, text=True)

        if result.returncode != 0:
            stderr = result.stderr.strip()
            raise click.ClickException(f"Unmount failed (exit code {result.returncode}): {stderr}")

        click.echo(f"Unmounted {mountpoint}")

    @staticmethod
    def _find_executable(name):
        """Find an executable in PATH, return full path or None"""
        import shutil
        return shutil.which(name)
