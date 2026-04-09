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
# Mount remote filesystem
j mount /local/mountpoint
j mount /local/mountpoint -r /remote/path
j mount /local/mountpoint --direct

# Unmount
j mount --umount /local/mountpoint
j mount --umount /local/mountpoint --lazy
```

## API Reference

### SSHMountClient

- `mount(mountpoint, *, remote_path="/", direct=False, extra_args=None)` - Mount remote filesystem locally via sshfs
- `umount(mountpoint, *, lazy=False)` - Unmount a previously mounted sshfs filesystem

### CLI

The driver registers as `mount` in the exporter config. When used in a `jmp shell` session, the CLI is a single command with a `--umount` flag for unmounting.
