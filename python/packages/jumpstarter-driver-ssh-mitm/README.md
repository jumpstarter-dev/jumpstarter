# SSH MITM Driver

`jumpstarter-driver-ssh-mitm` provides a secure SSH proxy layer where private keys
are stored on the exporter and never transmitted to clients. It is designed to be
used as a child of `SSHWrapper`.

## Installation

```{code-block} console
:substitutions:
$ pip3 install --extra-index-url {{index_url}} jumpstarter-driver-ssh-mitm
```

## Configuration

The command name is determined by the key in the `export` section. Use `ssh_mitm` to get the `j ssh_mitm` command:

```{literalinclude} ../../../../../packages/jumpstarter-driver-ssh-mitm/examples/config.yaml
:language: yaml
```

Or with inline key:

```{literalinclude} ../../../../../packages/jumpstarter-driver-ssh-mitm/examples/config_configuration.yaml
:language: yaml
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

## Architecture

```
SSHWrapper --> SSHMITM --> TcpNetwork --> DUT
```

- **SSHWrapper**: Handles SSH CLI and command execution
- **SSHMITM**: Provides authenticated proxy connection (stores the SSH key)
- **TcpNetwork**: Raw TCP connection to the DUT

## API Reference

```{eval-rst}
.. autoclass:: jumpstarter_driver_ssh_mitm.driver.SSHMITM()
```
