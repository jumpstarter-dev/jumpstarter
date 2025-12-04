# SSH MITM Driver

`jumpstarter-driver-ssh-mitm` provides secure SSH proxy functionality where private keys
are stored on the exporter and never transmitted to clients.

## Installation

```{code-block} console
:substitutions:
$ pip3 install --extra-index-url {{index_url}} jumpstarter-driver-ssh-mitm
```

## Configuration

Example configuration with inline key:

```yaml
export:
  ssh_mitm:
    type: jumpstarter_driver_ssh_mitm.driver.SSHMITM
    config:
      default_username: "root"
      ssh_identity: |
        -----BEGIN OPENSSH PRIVATE KEY-----
        ...
        -----END OPENSSH PRIVATE KEY-----
    children:
      tcp:
        type: jumpstarter_driver_network.driver.TcpNetwork
        config:
          host: "192.168.1.100"
          port: 22
```

Example configuration with key file:

```yaml
export:
  ssh_mitm:
    type: jumpstarter_driver_ssh_mitm.driver.SSHMITM
    config:
      default_username: "root"
      ssh_identity_file: "/path/to/private/key"
    children:
      tcp:
        type: jumpstarter_driver_network.driver.TcpNetwork
        config:
          host: "192.168.1.100"
          port: 22
```

### Config parameters

| Parameter         | Description                                              | Type | Required | Default |
| ----------------- | -------------------------------------------------------- | ---- | -------- | ------- |
| default_username  | Default SSH username                                     | str  | no       | ""      |
| ssh_identity      | SSH private key content (inline)                         | str  | no*      | None    |
| ssh_identity_file | Path to SSH private key file                             | str  | no*      | None    |

\* Either `ssh_identity` or `ssh_identity_file` must be provided.

### Required children

- `tcp`: A `TcpNetwork` driver providing target host and port

## Usage

```bash
# Execute a command
j ssh_mitm whoami

# Interactive shell (native SSH via port forwarding)
j ssh_mitm shell

# Interactive shell (gRPC REPL, no local SSH required)
j ssh_mitm shell --repl

# Port forward for ssh/scp/rsync
j ssh_mitm forward -p 2222
# Then: ssh -p 2222 localhost
```

## API Reference

```{eval-rst}
.. autoclass:: jumpstarter_driver_ssh_mitm.driver.SSHMITM()
```

```{eval-rst}
.. autoclass:: jumpstarter_driver_ssh_mitm.client.SSHMITMClient()
    :members: execute, run
```
