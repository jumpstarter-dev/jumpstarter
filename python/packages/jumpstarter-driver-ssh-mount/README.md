# SSHMount Driver

`jumpstarter-driver-ssh-mount` provides remote filesystem mounting via sshfs. It allows you to mount remote directories from a target device to your local machine using SSHFS (SSH Filesystem).

## Installation

```shell
pip3 install --extra-index-url https://pkg.jumpstarter.dev/simple/ jumpstarter-driver-ssh-mount
```

You also need `sshfs` installed on the client machine:

- **Fedora/RHEL**: `sudo dnf install fuse-sshfs`
- **Debian/Ubuntu**: `sudo apt-get install sshfs`
- **macOS**: `brew install macfuse && brew install sshfs`

## Configuration

Example exporter configuration:

```yaml
export:
  ssh-mount:
    type: jumpstarter_driver_ssh_mount.driver.SSHMount
    config:
      default_username: "root"
      # ssh_identity_file: "/path/to/ssh/key"
    children:
      tcp:
        type: jumpstarter_driver_network.driver.TcpNetwork
        config:
          host: "192.168.1.100"
          port: 22
```

## CLI Usage

Inside a `jmp shell` session:

```shell
# Mount remote filesystem
j ssh-mount mount /local/mountpoint
j ssh-mount mount /local/mountpoint -r /remote/path
j ssh-mount mount /local/mountpoint --direct

# Unmount
j ssh-mount umount /local/mountpoint
j ssh-mount umount /local/mountpoint --lazy
```

## API Reference

### SSHMountClient

- `mount(mountpoint, *, remote_path="/", direct=False, extra_args=None)` - Mount remote filesystem locally via sshfs
- `umount(mountpoint, *, lazy=False)` - Unmount a previously mounted sshfs filesystem
