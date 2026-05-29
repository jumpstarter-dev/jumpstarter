# SSHMount Driver

`jumpstarter-driver-ssh-mount` provides remote filesystem mounting via sshfs. It allows you to mount remote directories from a target device to your local machine using SSHFS (SSH Filesystem).

## Installation

```{code-block} shell
pip3 install --extra-index-url https://pkg.jumpstarter.dev/simple/ jumpstarter-driver-ssh-mount
```

You also need `sshfs` installed on the client machine:

- **Fedora/RHEL**: `sudo dnf install fuse-sshfs`
- **Debian/Ubuntu**: `sudo apt-get install sshfs`
- **macOS**: Install macFUSE and SSHFS from https://macfuse.github.io/, please note that
  it needs special handling to enable the macOS kernel extensions, read the install documentation
  carefully.

## Configuration

The SSHMount driver references an existing SSH driver to inherit credentials
(username, identity key) and TCP connectivity. No duplicate configuration is needed.

Example exporter configuration:

```{literalinclude} ../../../../../packages/jumpstarter-driver-ssh-mount/examples/config.yaml
:language: yaml
```

## CLI Usage

Inside a `jmp shell` session:

```{literalinclude} ../../../../../packages/jumpstarter-driver-ssh-mount/examples/usage_cli.bash
:language: shell
```

By default, `j mount` runs sshfs in foreground mode and spawns a subshell
with a modified prompt. The mount stays active while the subshell is running.
When you type `exit` (or press Ctrl+D), sshfs is terminated and all resources
(port forwards, temporary identity files) are cleaned up automatically.

Use `--foreground` to skip the subshell and block directly on sshfs. Press
Ctrl+C to unmount.

The `--umount` flag is available as a fallback for mounts that were orphaned
(e.g., if the process was killed without cleanup).

## API Reference

### SSHMountClient

- `mount(mountpoint, *, remote_path="/", direct=False, foreground=False, extra_args=None)` - Mount remote filesystem locally via sshfs
- `umount(mountpoint, *, lazy=False)` - Unmount an sshfs filesystem (fallback for orphaned mounts)

### Required Children

| Child name | Type | Description |
|-----------|------|-------------|
| `ssh` | `jumpstarter_driver_ssh.driver.SSHWrapper` | SSH driver providing credentials (username, identity key) and TCP connectivity. Must itself have a `tcp` child of type `TcpNetwork`. |

### CLI

The driver registers as `mount` in the exporter config. When used in a `jmp shell` session, the CLI is a single command with a `--umount` flag for unmounting.

Note: Each `-o` value is forwarded directly to sshfs as an `-o` option flag. You can
pass any option that sshfs (and by extension, the underlying SSH client) supports.
By default, the driver sets `StrictHostKeyChecking=accept-new`,
`UserKnownHostsFile=~/.ssh/known_hosts`, and `LogLevel=ERROR`. This means the first
connection to a host is accepted and remembered, and subsequent connections verify the
host key against the stored value. To disable host key verification entirely, pass
`--insecure`, which sets `StrictHostKeyChecking=no` and `UserKnownHostsFile=/dev/null`.
To override individual defaults, pass the replacement via `-o` (e.g.,
`-o StrictHostKeyChecking=yes`). Common options include `reconnect`, `cache=yes`,
`ServerAliveInterval=15`, and `compression=yes`. If you need other users on the
system to access the mounted filesystem, pass `-o allow_other` (requires
`user_allow_other` in `/etc/fuse.conf`). If `allow_other` fails due to FUSE
configuration, the mount will automatically retry without it.
