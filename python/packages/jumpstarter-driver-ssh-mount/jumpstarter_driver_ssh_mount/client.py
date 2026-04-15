from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from urllib.parse import urlparse

import click
from jumpstarter_driver_composite.client import CompositeClient
from jumpstarter_driver_network.adapters import TcpPortforwardAdapter

from jumpstarter.client.core import DriverMethodNotImplemented
from jumpstarter.client.decorators import driver_click_command

# Timeout in seconds for subprocess calls (mount test run, umount)
SUBPROCESS_TIMEOUT = 120


@dataclass(kw_only=True)
class SSHMountClient(CompositeClient):

    def cli(self):
        @driver_click_command(self)
        @click.argument("mountpoint", type=click.Path())
        @click.option("--umount", "-u", is_flag=True, help="Unmount instead of mount")
        @click.option("--remote-path", "-r", default="/", help="Remote path to mount (default: /)")
        @click.option("--direct", is_flag=True, help="Use direct TCP address")
        @click.option("--lazy", "-l", is_flag=True, help="Lazy unmount (detach filesystem now, clean up later)")
        @click.option("--foreground", is_flag=True, help="Block on sshfs in foreground without spawning a subshell")
        @click.option("--extra-args", "-o", multiple=True, help="Extra arguments to pass to sshfs")
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

    def mount(self, mountpoint, *, remote_path="/", direct=False, foreground=False, extra_args=None):
        """Mount remote filesystem locally via sshfs.

        Runs sshfs in foreground mode (-f) and spawns a subshell so that
        the mount stays alive while the user works. When the subshell exits,
        sshfs is terminated and all resources are cleaned up automatically.

        Args:
            mountpoint: Local directory to mount the remote filesystem on.
            remote_path: Remote path to mount (default: /).
            direct: If True, connect directly to the host's TCP address.
            foreground: If True, block on sshfs without spawning a subshell.
            extra_args: Extra arguments to pass to sshfs.
        """
        sshfs_path = self._find_executable("sshfs")
        if not sshfs_path:
            raise click.ClickException(
                "sshfs is not installed. Please install it:\n"
                "  Fedora/RHEL: sudo dnf install fuse-sshfs\n"
                "  Debian/Ubuntu: sudo apt-get install sshfs\n"
                "  macOS: Install macFUSE from https://macfuse.github.io/ and then install\n"
                "         sshfs from source, as Homebrew has removed sshfs support."
            )

        mountpoint = os.path.realpath(mountpoint)
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
                self._run_sshfs(host, port, mountpoint, remote_path, extra_args,
                                foreground=foreground)
            except (DriverMethodNotImplemented, ValueError) as e:
                self.logger.error(
                    "Direct address connection failed (%s), falling back to port forwarding", e
                )
                self.mount(mountpoint, remote_path=remote_path, direct=False,
                           foreground=foreground, extra_args=extra_args)
        else:
            self.logger.debug("Using SSH port forwarding for sshfs connection")
            with TcpPortforwardAdapter(client=self.ssh.tcp) as (host, port):
                self.logger.debug("SSH port forward established - host: %s, port: %s", host, port)
                self._run_sshfs(host, port, mountpoint, remote_path, extra_args,
                                foreground=foreground)

    def _run_sshfs(self, host, port, mountpoint, remote_path, extra_args, *, foreground):
        identity_file = self._create_temp_identity_file()
        sshfs_proc = None

        try:
            sshfs_args = self._build_sshfs_args(host, port, mountpoint, remote_path, identity_file, extra_args)
            # Add -f to run sshfs in foreground mode so it blocks
            sshfs_args.append("-f")

            self.logger.debug("Running sshfs command: %s", sshfs_args)

            # First try with allow_other; if that fails, retry without it
            sshfs_proc = self._start_sshfs_with_fallback(sshfs_args)

            default_username = self.username
            user_prefix = f"{default_username}@" if default_username else ""
            remote_spec = f"{user_prefix}{host}:{remote_path}"
            click.echo(f"Mounted {remote_spec} on {mountpoint}")

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
            # Terminate sshfs if it's still running
            if sshfs_proc is not None and sshfs_proc.poll() is None:
                sshfs_proc.terminate()
                try:
                    sshfs_proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    sshfs_proc.kill()
                    sshfs_proc.wait()

            # Run fusermount/umount to ensure clean unmount
            self._force_umount(mountpoint)
            click.echo(f"Unmounted {mountpoint}")
            self._cleanup_identity_file(identity_file)

    def _start_sshfs_with_fallback(self, sshfs_args):
        """Start sshfs, retrying without allow_other if it fails on that option.

        We do a quick test run (without -f) to check if sshfs can mount
        successfully, then start the real foreground process.
        """
        # Test run without -f to validate args quickly
        test_args = [a for a in sshfs_args if a != "-f"]
        result = subprocess.run(test_args, capture_output=True, text=True, timeout=SUBPROCESS_TIMEOUT)

        if result.returncode != 0 and "allow_other" in result.stderr:
            self.logger.debug("Retrying sshfs without allow_other option")
            sshfs_args = self._remove_allow_other(sshfs_args)
            # Test again without allow_other
            test_args = [a for a in sshfs_args if a != "-f"]
            result = subprocess.run(test_args, capture_output=True, text=True, timeout=SUBPROCESS_TIMEOUT)

        if result.returncode != 0:
            stderr = result.stderr.strip()
            raise click.ClickException(
                f"sshfs mount failed (exit code {result.returncode}): {stderr}"
            )

        # The test mount succeeded. Unmount it, then re-mount in foreground mode.
        # Extract the mountpoint from args (it's the 3rd arg: sshfs remote mount)
        mountpoint = sshfs_args[2]
        self._force_umount(mountpoint)

        # Now start the real foreground process
        proc = subprocess.Popen(
            sshfs_args,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )

        # Give sshfs a moment to start and check it hasn't failed immediately
        try:
            proc.wait(timeout=1)
            # If it exited already, something went wrong
            stderr = proc.stderr.read().decode() if proc.stderr else ""
            if proc.stderr:
                proc.stderr.close()
            raise click.ClickException(
                f"sshfs mount failed (exit code {proc.returncode}): {stderr.strip()}"
            )
        except subprocess.TimeoutExpired:
            # Good -- sshfs is running in foreground mode.
            # Close the stderr pipe to prevent deadlock: sshfs would block
            # if it fills the OS pipe buffer (~64KB) while we never read.
            if proc.stderr:
                proc.stderr.close()

        return proc

    def _remove_allow_other(self, sshfs_args):
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
        return filtered

    def _run_subshell(self, mountpoint, remote_path):
        """Spawn an interactive subshell with a modified prompt."""
        shell = os.environ.get("SHELL", "/bin/sh")
        env = os.environ.copy()

        # Modify the prompt to indicate the active mount
        prompt_prefix = f"[sshfs:{remote_path}] "
        try:
            if "bash" in shell:
                env["PS1"] = prompt_prefix + env.get("PS1", r"\$ ")
                subprocess.run(
                    [shell, "--norc", "--noprofile", "-i"],
                    env=env,
                )
            elif "zsh" in shell:
                env["PS1"] = prompt_prefix + env.get("PS1", "%# ")
                subprocess.run([shell, "-i"], env=env)
            else:
                subprocess.run([shell, "-i"], env=env)
        except FileNotFoundError:
            raise click.ClickException(
                f"Shell '{shell}' not found. Set the SHELL environment variable to a valid shell."
            )

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
            for arg in extra_args:
                sshfs_args.extend(["-o", arg])

        return sshfs_args

    def _create_temp_identity_file(self):
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
                    temp_file.close()
                except Exception:
                    pass
                try:
                    os.unlink(temp_file.name)
                except Exception:
                    pass
            raise

    def _cleanup_identity_file(self, identity_file):
        if identity_file:
            try:
                os.unlink(identity_file)
                self.logger.debug("Cleaned up temporary identity file: %s", identity_file)
            except Exception as e:
                self.logger.warning("Failed to clean up identity file %s: %s", identity_file, e)

    def umount(self, mountpoint, *, lazy=False):
        """Unmount an sshfs filesystem (fallback for orphaned mounts)."""
        mountpoint = os.path.realpath(mountpoint)
        cmd = self._build_umount_cmd(mountpoint, lazy=lazy)

        self.logger.debug("Running unmount command: %s", cmd)
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=SUBPROCESS_TIMEOUT)

        if result.returncode != 0:
            stderr = result.stderr.strip()
            raise click.ClickException(f"Unmount failed (exit code {result.returncode}): {stderr}")

        click.echo(f"Unmounted {mountpoint}")

    def _force_umount(self, mountpoint):
        """Best-effort unmount, ignoring errors (used during cleanup)."""
        cmd = self._build_umount_cmd(mountpoint, lazy=False)
        try:
            subprocess.run(cmd, capture_output=True, text=True, timeout=SUBPROCESS_TIMEOUT)
        except Exception:
            pass

    def _build_umount_cmd(self, mountpoint, *, lazy=False):
        fusermount = self._find_executable("fusermount3") or self._find_executable("fusermount")
        if fusermount:
            cmd = [fusermount, "-u"]
            if lazy:
                cmd.append("-z")
        else:
            cmd = ["umount"]
            if lazy:
                # macOS umount does not support -l; use -f (force) instead
                if sys.platform == "darwin":
                    cmd.append("-f")
                else:
                    cmd.append("-l")
        cmd.append(mountpoint)
        return cmd

    @staticmethod
    def _find_executable(name):
        return shutil.which(name)
