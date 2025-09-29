# SSHWrapper Driver

`jumpstarter-driver-ssh` provides SSH CLI functionality for Jumpstarter, allowing you to run SSH commands with configurable defaults and pass-through arguments.

## Installation

```shell
pip3 install --extra-index-url https://pkg.jumpstarter.dev/simple/ jumpstarter-driver-ssh
```

## Configuration

Example configuration:

```yaml
export:
  ssh:
    type: jumpstarter_driver_ssh.driver.SSHWrapper
    config:
      default_username: "root"
      ssh_command: "ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null"
    children:
      tcp:
        type: jumpstarter_driver_network.driver.TcpNetwork
        config:
          host: "192.168.1.100"
          port: 22
```

## Usage

The SSH driver provides a CLI command that accepts all standard SSH arguments:

```bash
# Basic SSH connection (uses port forwarding by default)
j ssh

# SSH with direct TCP address
j ssh --direct

# SSH with specific user
j ssh -l myuser

# SSH with other flags
j ssh -i ~/.ssh/id_rsa

# Running a remote command
j ssh ls -la

```

## CLI Options

The SSH command supports the following options:

- `--direct`: Use direct TCP address (default is port forwarding)

All other arguments are passed directly to the SSH command. The driver uses the configured SSH command and default username from the driver configuration.

### Username Handling

The driver supports multiple ways to specify the username:

1. **`-l username` flag**: Explicit username specification (takes precedence)
2. **Default username**: Used when no username is specified in arguments

If no `-l` flag or `user@hostname` format is provided, the default username from the driver configuration will be used automatically.

## Dependencies

- `ssh`: Standard SSH client (usually pre-installed)

## API Reference

### Driver Methods

```{eval-rst}
.. autoclass:: jumpstarter_driver_ssh.client.SSHWrapperClient()
    :members: run
```


### Configuration Parameters

| Parameter        | Description                                                                                    | Type | Required | Default                                                                                    |
| ---------------- | ---------------------------------------------------------------------------------------------- | ---- | -------- | ------------------------------------------------------------------------------------------ |
| default_username | Default SSH username to use when no username is specified in the command                      | str  | no       | ""                                                                                         |
| ssh_command      | SSH command to use for connections                                                             | str  | no       | "ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR"      |

### Required Children

- `tcp`: A TcpNetwork driver instance that provides the connection details (host and port)