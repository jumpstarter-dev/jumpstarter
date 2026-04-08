import os
import shlex
import subprocess
import tempfile
from contextlib import asynccontextmanager
from dataclasses import dataclass
from urllib.parse import urlparse

import click
from jumpstarter_driver_composite.client import CompositeClient
from jumpstarter_driver_network.adapters import TcpPortforwardAdapter

from jumpstarter.client.core import DriverMethodNotImplemented


@dataclass
class SSHCommandRunResult:
    """Result of executing an SSH command"""
    return_code: int
    stdout: str | bytes
    stderr: str | bytes

    @staticmethod
    def from_completed_process(result: subprocess.CompletedProcess) -> "SSHCommandRunResult":
        return SSHCommandRunResult(
            return_code=result.returncode,
            stdout=result.stdout or "",
            stderr=result.stderr or "",
        )


@dataclass
class SSHCommandRunOptions:
    """
    Options for running an SSH command

    Attributes:
        direct: If True, connect directly to the host's TCP address.
                If False, use SSH port forwarding.
        capture_output: If True, capture stdout and stderr.
                        If False, they are inherited from the parent process.
        capture_as_text: If True and output is captured, decode stdout and
                         stderr as text. Otherwise, they are captured as bytes.
    """
    direct: bool = False
    capture_output: bool = True
    capture_as_text: bool = True


@dataclass(kw_only=True)
class SSHWrapperClient(CompositeClient):
    """
    Client interface for SSHWrapper driver

    This client provides methods to interact with SSH connections via CLI
    """

    def cli(self):
        from jumpstarter.client.decorators import driver_click_group

        @driver_click_group(self)
        def ssh_group():
            """SSH driver with shell, mount, and umount commands"""
            pass

        @ssh_group.command(
            "shell",
            context_settings={"ignore_unknown_options": True},
        )
        @click.option("--direct", is_flag=True, help="Use direct TCP address")
        @click.argument("args", nargs=-1)
        def ssh_shell(direct, args):
            """Run SSH command with arguments"""
            options = SSHCommandRunOptions(
                direct=direct,
                # For the CLI, we never capture output so that interactive shells
                # and long-running commands stream their output directly.
                capture_output=False,
            )

            result = self.run(options, args)
            self.logger.debug("SSH exit code: %s", result.return_code)

            if result.stdout:
                click.echo(result.stdout, nl=False)
            if result.stderr:
                click.echo(result.stderr, nl=False, err=True)

            if result.return_code != 0:
                click.get_current_context().exit(result.return_code)

            return result.return_code

        @ssh_group.command("mount")
        @click.argument("mountpoint", type=click.Path())
        @click.option("--remote-path", "-r", default="/", help="Remote path to mount (default: /)")
        @click.option("--direct", is_flag=True, help="Use direct TCP address")
        @click.option("--extra-args", "-o", multiple=True, help="Extra arguments to pass to sshfs")
        def ssh_mount(mountpoint, remote_path, direct, extra_args):
            """Mount remote filesystem locally via sshfs"""
            self.mount(mountpoint, remote_path=remote_path, direct=direct, extra_args=list(extra_args))

        @ssh_group.command("umount")
        @click.argument("mountpoint", type=click.Path(exists=True))
        @click.option("--lazy", "-l", is_flag=True, help="Lazy unmount (detach filesystem now, clean up later)")
        def ssh_umount(mountpoint, lazy):
            """Unmount a previously mounted sshfs filesystem"""
            self.umount(mountpoint, lazy=lazy)

        return ssh_group

    # wrap the underlying tcp stream connections, so we can still use tcp forwarding or
    # the fabric driver adapter on top of client.ssh
    @asynccontextmanager
    async def stream_async(self, method):
        async with self.tcp.stream_async(method) as stream:
            yield stream

    @property
    def command(self) -> str:
        """Get the base SSH command"""
        return self.call("get_ssh_command")

    @property
    def identity(self) -> str | None:
        """
        Get the SSH identity (private key) as a string.

        Returns:
            The SSH identity key content, or None if not configured.

        Raises:
            ConfigurationError: If `ssh_identity_file` is configured on the
                                driver but cannot be read.
        """
        return self.call("get_ssh_identity")

    @property
    def username(self) -> str:
        """Get the default SSH username"""
        return self.call("get_default_username")

    def run(self, options: SSHCommandRunOptions, args) -> SSHCommandRunResult:
        """Run SSH command with the given parameters and arguments"""
        # Get SSH command and default username from driver
        if options.direct:
            # Use direct TCP address
            try:
                address = self.tcp.address()  # (format: "tcp://host:port")
                parsed = urlparse(address)
                host = parsed.hostname
                port = parsed.port
                if not host or not port:
                    raise ValueError(f"Invalid address format: {address}")
                self.logger.debug("Using direct TCP connection for SSH - host: %s, port: %s", host, port)
                return self._run_ssh_local(host, port, options, args)
            except (DriverMethodNotImplemented, ValueError) as e:
                self.logger.error("Direct address connection failed (%s), falling back to SSH port forwarding", e)
                return self.run(SSHCommandRunOptions(
                    direct=False,
                    capture_output=options.capture_output,
                    capture_as_text=options.capture_as_text,
                ), args)
        else:
            # Use SSH port forwarding (default behavior)
            self.logger.debug("Using SSH port forwarding for SSH connection")
            with TcpPortforwardAdapter(
                client=self.tcp,
            ) as addr:
                host, port = addr
                self.logger.debug("SSH port forward established - host: %s, port: %s", host, port)
                return self._run_ssh_local(host, port, options, args)

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
                address = self.tcp.address()
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
            with TcpPortforwardAdapter(client=self.tcp) as addr:
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
            click.echo(f"To unmount: j ssh umount {mountpoint}")
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

    def _run_ssh_local(self, host, port, options, args):
        """Run SSH command with the given host, port, and arguments"""
        # Create temporary identity file if needed
        ssh_identity = self.identity
        identity_file = None
        temp_file = None
        if ssh_identity:
            try:
                temp_file = tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='_ssh_key')
                temp_file.write(ssh_identity)
                temp_file.close()
                # Set proper permissions (600) for SSH key
                os.chmod(temp_file.name, 0o600)
                identity_file = temp_file.name
                self.logger.debug("Created temporary identity file: %s", identity_file)
            except Exception as e:
                self.logger.error("Failed to create temporary identity file: %s", e)
                if temp_file:
                    try:
                        os.unlink(temp_file.name)
                    except Exception:
                        pass
                raise

        try:
            # Build SSH command arguments
            ssh_args = self._build_ssh_command_args(port, identity_file, args)

            # Separate SSH options from command arguments
            ssh_options, command_args = self._separate_ssh_options_and_command_args(args)

            # Build final SSH command
            ssh_args = self._build_final_ssh_command(ssh_args, ssh_options, host, command_args)

            # Execute the command
            return self._execute_ssh_command(ssh_args, options)
        finally:
            # Clean up temporary identity file
            if identity_file:
                try:
                    os.unlink(identity_file)
                    self.logger.debug("Cleaned up temporary identity file: %s", identity_file)
                except Exception as e:
                    self.logger.warning("Failed to clean up temporary identity file %s: %s", identity_file, str(e))

    def _build_ssh_command_args(self, port, identity_file, args):
        """Build initial SSH command arguments"""
        # Split the SSH command into individual arguments
        ssh_args = shlex.split(self.command)
        default_username = self.username

        # Add identity file if provided
        if identity_file:
            ssh_args.extend(["-i", identity_file])

        # Add port if specified
        if port and port != 22:
            ssh_args.extend(["-p", str(port)])

        # Check if user already provided a username with -l flag in SSH options only
        # We need to separate SSH options from command args first to avoid false positives
        ssh_options, _ = self._separate_ssh_options_and_command_args(args)
        has_user_flag = any(
            ssh_options[i] == "-l" and i + 1 < len(ssh_options)
            for i in range(len(ssh_options))
        )

        # Add default username if no -l flag provided and we have a default
        if not has_user_flag and default_username:
            ssh_args.extend(["-l", default_username])

        return ssh_args


    def _separate_ssh_options_and_command_args(self, args):
        """Separate SSH options from command arguments"""
        # SSH flags that do not expect a parameter (simple flags)
        ssh_flags_no_param = {
            '-4', '-6', '-A', '-a', '-C', '-f', '-G', '-g', '-K', '-k', '-M', '-N',
            '-n', '-q', '-s', '-T', '-t', '-V', '-v', '-X', '-x', '-Y', '-y'
        }

        # SSH flags that do expect a parameter
        ssh_flags_with_param = {
            '-B', '-b', '-c', '-D', '-E', '-e', '-F', '-I', '-i', '-J', '-L', '-l',
            '-m', '-O', '-o', '-P', '-p', '-Q', '-R', '-S', '-W', '-w'
        }

        ssh_options = []
        command_args = []
        i = 0
        while i < len(args):
            arg = args[i]
            if arg.startswith('-'):
                # Check if it's a known SSH option
                if arg in ssh_flags_no_param:
                    # This is a simple SSH flag without parameter
                    ssh_options.append(arg)
                elif arg in ssh_flags_with_param:
                    # This is an SSH flag that expects a parameter
                    ssh_options.append(arg)
                    # If this option takes a value, add the next argument too
                    if i + 1 < len(args) and not args[i + 1].startswith('-'):
                        ssh_options.append(args[i + 1])
                        i += 1
                else:
                    # This is a command argument - everything from here on is part of the command
                    command_args = args[i:]
                    break
            else:
                # This is a command argument - everything from here on is part of the command
                command_args = args[i:]
                break
            i += 1

        # Debug output
        self.logger.debug("SSH options: %s", ssh_options)
        self.logger.debug("Command args: %s", command_args)
        return ssh_options, command_args


    def _build_final_ssh_command(self, ssh_args, ssh_options, host, command_args):
        """Build the final SSH command with all components"""
        # Add SSH options
        ssh_args.extend(ssh_options)

        # Add hostname before command arguments
        if host:
            ssh_args.append(host)

        # Add command arguments
        ssh_args.extend(command_args)

        self.logger.debug("Running SSH command: %s", ssh_args)
        return ssh_args

    def _execute_ssh_command(self, ssh_args, options: SSHCommandRunOptions) -> SSHCommandRunResult:
        """Execute the SSH command and return the result"""
        try:
            result = subprocess.run(ssh_args, capture_output=options.capture_output, text=options.capture_as_text)
            return SSHCommandRunResult.from_completed_process(result)
        except FileNotFoundError:
            self.logger.error(
                "SSH command '%s' not found. Please ensure SSH is installed and available in PATH.",
                ssh_args[0],
            )
            return SSHCommandRunResult(
                return_code=127,  # Standard exit code for "command not found"
                stdout="",
                stderr=f"SSH command '{ssh_args[0]}' not found",
            )
