# Files

This page describes configuration files used in Jumpstarter, including their
format, location, related environment variables, and management commands.

Jumpstarter follows a specific hierarchy when loading configurations. See
[Loading Order](loading-order.md) for details on how configurations from
different sources are prioritized.

## User Configuration

**File**: `config.yaml`  
**Location**: `/home/<user>/.config/jumpstarter/config.yaml`  
**Description**: Defines global user settings including current client
selection.  

**Format**:
```{code-block} yaml
apiVersion: jumpstarter.dev/v1alpha1
kind: UserConfig
config:
  current-client: default # The currently selected client config to use (by local alias).
```

**CLI Commands**: The current client selection can be changed using: `jmp config client use <alias>`

## Client Configuration

**File**: All valid client configuration files with a `.yaml` extension.  
**Location**: `/home/<user>/.config/jumpstarter/clients/*.yaml`  
**Description**: Stores client configurations including endpoints, access
tokens, and driver settings.

**Format**:
```{code-block} yaml
apiVersion: jumpstarter.dev/v1alpha1
kind: ClientConfig
metadata:
  name: myclient # The name of the client in the cluster.
  namespace: jumpstarter-lab # The namespace of the client in the cluster.
tls:
  insecure: false # Enable insecure TLS for testing and development
  ca: "" # A CA certificate to use
endpoint: "jumpstarter.my-lab.com:1443" # The Jumpstarter service endpoint
token: "******************" # An authentication token
drivers:
  allow: ["jumpstarter_drivers_*", "vendorpackage.*"] # Driver packages the client can dynamically load
  unsafe: false # Allow any driver package to load dynamically
```

**Environment Variables**:

- `JUMPSTARTER_GRPC_INSECURE` / `JMP_GRPC_INSECURE` - Set to `1` to disable TLS verification globally
- `JMP_CLIENT_CONFIG` - Path to a client configuration file
- `JMP_CLIENT` - Name of a registered client config
- `JMP_NAMESPACE` - Namespace in the {term}`controller`
- `JMP_NAME` - Client name
- `JMP_ENDPOINT` - {term}`gRPC` endpoint (overrides config file)
- `JMP_TOKEN` - Auth token (overrides config file)
- `JMP_DRIVERS_ALLOW` - Comma-separated list of allowed driver namespaces
- `JUMPSTARTER_FORCE_SYSTEM_CERTS` - Set to `1` to force system CA certificates

**CLI Commands**:
```{code-block}  console
$ jmp config client create <alias>  # Create new empty client config
$ jmp config client use <alias>     # Switch to a different client config
$ jmp config client list            # List available client configs
$ jmp config client delete <alias>  # Remove a client config locally
```

## Exporter Configuration

**File**: All valid {term}`exporter` configuration files with a `.yaml` extension  
**Location**: `~/.config/jumpstarter/exporters/*.yaml` (per-user) or `/etc/jumpstarter/exporters/*.yaml` (system-wide)

**Description**: Defines {term}`exporter` settings including connection details and
driver configurations.

Exporter configs created via the CLI default to the per-user location. Both the per-user and
system locations are searched when loading, with the per-user path taking precedence. The user
config home can be overridden with `JMP_CLIENT_CONFIG_HOME` or `XDG_CONFIG_HOME`. The
`jmp admin create exporter` and `jmp admin import exporter` commands also accept `--out <file>`
to write the config to an explicit path.

**Format**:
```{code-block} yaml
apiVersion: jumpstarter.dev/v1alpha1
kind: ExporterConfig
metadata:
  name: myexporter # The name of the exporter in the cluster.
  namespace: jumpstarter-lab # The namespace of the exporter in the cluster.
tls:
  insecure: false # Enable insecure TLS for testing and development
  ca: "" # A CA certificate to use
endpoint: "jumpstarter.my-lab.com:1443" # The Jumpstarter service endpoint
token: "******************" # An authentication token
export: # Configure drivers to expose to the clients
  power:
    type: "jumpstarter_driver_power.driver.PduPower" # The driver Python class path and type
    config: # Dynamic configuration dict passed to the driver class
      host: "192.168.1.111"
      port: 1234
      username: "admin"
      password: "secret"
  serial:
    type: "jumpstarter_driver_pyserial.driver.PySerial"
    config:
      url: "/dev/ttyUSB0"
      baudrate: 115200
hooks: # Optional lifecycle hooks that run at lease boundaries
  beforeLease:
    script: |
      j power on
    timeout: 120       # Hook execution timeout in seconds (default: 120)
    onFailure: warn    # Action on failure: "warn" (default), "endLease", or "exit"
  afterLease:
    script: |
      j power off
    timeout: 30
    onFailure: warn
```

The optional `hooks` section configures lifecycle scripts that run at {term}`lease`
boundaries. See [{term}`Hook`s](../../introduction/hooks.md) for full details on
{term}`hook` configuration, environment variables, and failure handling.

**Environment Variables**:
- `JUMPSTARTER_GRPC_INSECURE` / `JMP_GRPC_INSECURE` - Set to `1` to disable TLS verification
- `JMP_ENDPOINT` - {term}`gRPC` endpoint (overrides config file)
- `JMP_TOKEN` - Auth token (overrides config file)
- `JMP_NAMESPACE` - Namespace in the {term}`controller`
- `JMP_NAME` - {term}`Exporter` name

**CLI Commands**:
```{code-block}  console
$ jmp config exporter create <alias>  # Create new empty exporter config
$ jmp config exporter list            # List available exporter configs
$ jmp config exporter delete <alias>  # Remove a local exporter config
```

## Running Exporters

### Running from the CLI

Exporters can be run manually from the command line for testing:

```{code-block} console
# Run using the exporter config alias (the config file name)
# The exporter process will exit when the lease is released
$ jmp run --exporter my-exporter

# Run using the path to a specific exporter config file
$ jmp run --exporter-config /etc/jumpstarter/exporters/my-exporter.yaml
```

### Running as a Service

For production deployments, it is recommended to use a service manager such as [`systemd`](https://systemd.io/) to keep the {term}`exporter` process alive and restart it after a {term}`lease` ends or something goes wrong.

Containerized {term}`exporter`s can be installed as [`systemd`](https://systemd.io/) services using [`podman-systemd`](https://docs.podman.io/en/latest/markdown/podman-systemd.unit.5.html).

Create a `systemd` service file at `/etc/containers/systemd/my-exporter.container` with the following content:

```{code-block} ini
:substitutions:
[Unit]
Description=My exporter

[Container]
ContainerName=my-exporter
Exec=/jumpstarter/bin/jmp run --exporter my-exporter # Command to run inside the container
Image=quay.io/jumpstarter-dev/jumpstarter:{{version}} # The container image to use
Network=host # Use host networking
PodmanArgs=--privileged # Enable privileged mode to allow hardware access
Volume=/run/udev:/run/udev # Support devices within the container
Volume=/dev:/dev # Support devices within the container
Volume=/etc/jumpstarter:/etc/jumpstarter # Mount Jumpstarter configs directory

[Service]
Restart=always # Always restart the container after exit
StartLimitBurst=0

[Install]
WantedBy=multi-user.target default.target
```

Then enable and start the service:

```{code-block} console
$ sudo systemctl daemon-reload
$ sudo systemctl enable --now my-exporter
```
