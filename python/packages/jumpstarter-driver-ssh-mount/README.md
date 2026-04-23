# SSHMount Driver

`jumpstarter-driver-ssh-mount` provides remote filesystem mounting via sshfs. It allows you to mount remote directories from a target device to your local machine using SSHFS (SSH Filesystem).

## Installation

```shell
pip3 install --extra-index-url https://pkg.jumpstarter.dev/simple/ jumpstarter-driver-ssh-mount
```

You also need `sshfs` installed on the client machine:

- **Fedora/RHEL**: `sudo dnf install fuse-sshfs`
- **Debian/Ubuntu**: `sudo apt-get install sshfs`
- **macOS**: Install macFUSE from https://macfuse.github.io/ and then install
  sshfs from source, as Homebrew has removed sshfs support.

## Configuration

The SSHMount driver references an existing SSH driver to inherit credentials
(username, identity key) and TCP connectivity. No duplicate configuration is needed.

Example exporter configuration:

```yaml
export:
  ssh:
    type: jumpstarter_driver_ssh.driver.SSHWrapper
    config:
      default_username: "root"
      # ssh_identity_file: "/path/to/ssh/key"
    children:
      tcp:
        type: jumpstarter_driver_network.driver.TcpNetwork
        config:
          host: "192.168.1.100"
          port: 22
  mount:
    type: jumpstarter_driver_ssh_mount.driver.SSHMount
    children:
      ssh:
        ref: "ssh"
```

## CLI Usage

Inside a `jmp shell` session:

```shell
# Mount remote filesystem (spawns a subshell; type 'exit' to unmount)
j mount /local/mountpoint
j mount /local/mountpoint -r /remote/path
j mount /local/mountpoint --direct

# Mount in foreground mode (blocks until Ctrl+C)
j mount /local/mountpoint --foreground

# Pass extra sshfs options
j mount /local/mountpoint -o reconnect -o cache=yes

# Unmount an orphaned mount
j mount --umount /local/mountpoint
j mount --umount /local/mountpoint --lazy
```

By default, `j mount` runs sshfs in foreground mode and spawns a subshell
with a modified prompt. The mount stays active while the subshell is running.
When you type `exit` (or press Ctrl+D), sshfs is terminated and all resources
(port forwards, temporary identity files) are cleaned up automatically.

Use `--foreground` to skip the subshell and block directly on sshfs. Press
Ctrl+C to unmount.

The `--umount` flag is available as a fallback for mounts that were orphaned
(e.g., if the process was killed without cleanup).

## Security: `allow_other` mount option

By default, sshfs is invoked with `-o allow_other`, which permits all local
users to access the mounted filesystem — not just the user who ran `j mount`.
This is convenient for build workflows where tools run under different UIDs,
but it has security implications on multi-user systems:

- Any local user can read (and potentially write) files on the remote device
  through the mountpoint.
- The option requires that `/etc/fuse.conf` contains `user_allow_other`;
  otherwise the mount will fail.

**Automatic fallback:** if `allow_other` is rejected by FUSE (e.g.,
`user_allow_other` is not set), the driver automatically retries the mount
without it. In that case only the mounting user can access the filesystem.

To explicitly disable `allow_other` without relying on the fallback, you can
override the option via `--extra-args`:

```shell
j mount /mnt/device -o allow_other=0
```

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

Note: `extra_args` values (passed via `-o`) are forwarded directly to sshfs. This
can be used to override defaults such as `StrictHostKeyChecking=no` -- for example,
`-o StrictHostKeyChecking=yes`.
