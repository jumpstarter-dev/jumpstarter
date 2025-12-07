# SSH MITM Driver

`jumpstarter-driver-ssh-mitm` provides a secure SSH proxy layer where private keys
are stored on the exporter and never transmitted to clients. It is designed to be
used as a child of `SSHWrapper`.

## Installation

```{code-block} console
:substitutions:
$ pip3 install --extra-index-url {{index_url}} jumpstarter-driver-ssh-mitm
```

## Architecture

```
SSHWrapper --> SSHMITM --> TcpNetwork --> DUT
```

- **SSHWrapper**: Handles SSH CLI and command execution
- **SSHMITM**: Provides authenticated proxy connection (stores the SSH key)
- **TcpNetwork**: Raw TCP connection to the DUT

## Configuration

The command name is determined by the key in the `export` section. Use `ssh_mitm` to get the `j ssh_mitm` command:

```yaml
export:
  ssh_mitm:  # ← This gives you "j ssh_mitm" command
    type: jumpstarter_driver_ssh.driver.SSHWrapper
    config:
      default_username: root
    children:
      tcp:
        type: jumpstarter_driver_ssh_mitm.driver.SSHMITM
        config:
          ssh_identity_file: /path/to/private/key
          default_username: root
        children:
          tcp:
            type: jumpstarter_driver_network.driver.TcpNetwork
            config:
              host: 192.168.1.100
              port: 22
```

Or with inline key:

```yaml
export:
  ssh_mitm:  # ← This gives you "j ssh_mitm" command
    type: jumpstarter_driver_ssh.driver.SSHWrapper
    config:
      default_username: root
    children:
      tcp:
        type: jumpstarter_driver_ssh_mitm.driver.SSHMITM
        config:
          default_username: root
          ssh_identity: |
            -----BEGIN OPENSSH PRIVATE KEY-----
            ...
            -----END OPENSSH PRIVATE KEY-----
        children:
          tcp:
            type: jumpstarter_driver_network.driver.TcpNetwork
            config:
              host: 192.168.1.100
              port: 22
```

### SSHMITM Config parameters

| Parameter         | Description                              | Type  | Required | Default |
| ----------------- | ---------------------------------------- | ----- | -------- | ------- |
| default_username  | SSH username for DUT connection          | str   | no       | ""      |
| ssh_identity      | SSH private key content (inline)         | str   | no*      | None    |
| ssh_identity_file | Path to SSH private key file             | str   | no*      | None    |

\* Either `ssh_identity` or `ssh_identity_file` must be provided.

### Required children

- `tcp`: A `TcpNetwork` driver providing target host and port

## Usage

Since SSHMITM is used as a child of SSHWrapper, you use the configured command name (e.g., `ssh_mitm`):

```bash
# Execute a command
j ssh_mitm whoami

# Interactive shell
j ssh_mitm

# With arguments
j ssh_mitm ls -la /tmp

# With SSH flags
j ssh_mitm -v hostname
```

**Note**: The command name (`ssh_mitm`) is determined by the key in your exporter config's `export` section. You can use any name you prefer.

## API Reference

```{eval-rst}
.. autoclass:: jumpstarter_driver_ssh_mitm.driver.SSHMITM()
```
