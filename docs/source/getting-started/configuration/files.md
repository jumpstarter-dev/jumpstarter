# Files

This page describes config files used in Jumpstarter, including their
format, location, related environment variables, and management commands.

Jumpstarter follows a specific hierarchy when loading configurations. See
[Loading Order](loading-order.md) for details on how configurations from
different sources are prioritized.

Jumpstarter saves the user, client, and {term}`exporter` config files
atomically and with mode `600`, since client and {term}`exporter` configs hold
tokens.

## User Config

**File**: `config.yaml`  
**Location**: `/home/<user>/.config/jumpstarter/config.yaml`  
**Description**: Defines global user settings including current client
selection.  

**Format**:
```yaml
apiVersion: jumpstarter.dev/v1alpha1
kind: UserConfig
config:
  current-client: default # The currently selected client config to use (by local alias).
```

**CLI Commands**: The current client selection can be changed using: `jmp config client use <alias>`

## Client Config

**File**: All valid client config files with a `.yaml` extension.  
**Location**: `/home/<user>/.config/jumpstarter/clients/*.yaml`  
**Description**: Stores client configs including endpoints, access
tokens, and driver settings.

**Format**:
```yaml
apiVersion: jumpstarter.dev/v1alpha1
kind: ClientConfig
metadata:
  name: myclient # The name of the client in the cluster.
  namespace: jumpstarter-lab # The namespace of the client in the cluster.
tls:
  insecure: false # Enable insecure TLS for testing and development
  ca: "" # A base64-encoded PEM CA certificate bundle to trust
endpoint: "jumpstarter.my-lab.com:1443" # The Jumpstarter service endpoint
token: "******************" # An authentication token
drivers:
  allow: ["jumpstarter_driver_*", "vendorpackage.*"] # Globs matched against the full class path of driver clients
  unsafe: false # Allow any driver package to load dynamically
leases:
  acquisition_timeout: 7200 # Timeout in seconds for lease acquisition (default: 7200)
  dial_timeout: 60 # Time limit for the exporter to become ready after lease is acquired (default: 60)
  retry_timeout: 300 # Time limit for re-attempting a lease when the exporter becomes unreachable (default: 300, 0 to disable)
```

**Environment Variables**:

- `JUMPSTARTER_GRPC_INSECURE` / `JMP_GRPC_INSECURE` - Set to `1` to disable TLS verification globally
- `JMP_NAMESPACE` - Namespace in the {term}`controller`
- `JMP_NAME` - Client name
- `JMP_ENDPOINT` - {term}`gRPC` endpoint
- `JMP_TOKEN` - Auth token
- `JMP_DRIVERS_ALLOW` - Comma-separated list of allowed driver namespaces
- `JUMPSTARTER_FORCE_SYSTEM_CERTS` - Set to `1` to force system CA certificates
- `JMP_RETRY_TIMEOUT` - Retry timeout in seconds for unreachable exporters (overrides config, default: 300)
- `JMP_DIAL_TIMEOUT` - Dial timeout in seconds for slow exporters (overrides config, default: 60)
- `JMP_OIDC_CALLBACK_PORT` - Local port for the OIDC callback during `jmp login` (useful for SSH tunneling; default: OS-assigned)
- `JMP_OIDC_DEVICE_FLOW` - Set to `1` to log in with the OAuth 2.0 Device Authorization Grant instead of a local callback, also when the CLI re-authenticates automatically (see [Authentication](authentication.md))
- `JMP_GRPC_PASSPHRASE` - Shared passphrase for authenticating against passphrase-protected exporters
- `NO_COLOR` - Set to any value (including empty) to disable ANSI color sequences in the `jmp shell` prompt (see [no-color.org](https://no-color.org/); does not affect status icons)
- `NO_ICONS` - Set to any value (including empty) to force ASCII status icons and the `jmp shell` prompt (e.g. `jumpstarter ^<exporter> >`)

`JMP_NAMESPACE`, `JMP_NAME`, `JMP_ENDPOINT`, `JMP_TOKEN` and `JMP_DRIVERS_ALLOW`
only supply values that the loaded client config file leaves out. When neither
`--client` nor `--client-config` is given and both `JMP_NAME` and
`JMP_NAMESPACE` are set, the CLI builds the client config from these variables
instead of loading the current client. See [Loading Order](loading-order.md).

**Exporter Shell Variables** (automatically set by `jmp shell`):

- `JMP_LEASE` - Active lease name (enables reconnection via `JMP_LEASE=<name> jmp shell`)
- `JMP_EXPORTER` - Name of the connected exporter
- `JMP_EXPORTER_LABELS` - Connected exporter's labels as comma-separated `key=value` pairs

**CLI Commands**:
```{code-block}  console
$ jmp config client create <alias>  # Create a client config
$ jmp config client use <alias>     # Switch to a different client config
$ jmp config client list            # List available client configs
$ jmp config client delete <alias>  # Remove a client config locally
```

`jmp config client list -o json` and `-o yaml` leave out the tokens unless you
add `--show-credentials`. `jmp config client create` and
`jmp config exporter create` accept `--insecure-tls` (`-k`) to write
`tls.insecure: true`.

**Python API compatibility**: `ClientConfigV1Alpha1.save(config)` now uses
`config.path` when it is set, preserving the location of a loaded configuration.
Previously, omitting the destination always saved to the alias path in the
clients directory. New configurations with `path=None` still use the alias
path, and an explicit `path` argument takes precedence. Callers that require
the previous behavior should explicitly pass
`ClientConfigV1Alpha1.CLIENT_CONFIGS_PATH / f"{config.alias}.yaml"` as the
destination.

## Exporter Config

**File**: All valid {term}`exporter` config files with a `.yaml` extension  
**Location**: `~/.config/jumpstarter/exporters/*.yaml` (per-user) or `/etc/jumpstarter/exporters/*.yaml` (system-wide)

**Description**: Defines {term}`exporter` settings including connection details and
driver configs.

Exporter configs created via the CLI default to the per-user location. Both the per-user and
system locations are searched when loading, with the per-user path taking precedence. The user
config home can be overridden with `JMP_CLIENT_CONFIG_HOME` or `XDG_CONFIG_HOME`. The
`jmp admin create exporter` and `jmp admin import exporter` commands also accept `--out <file>`
to write the config to an explicit path.

**Format**:
```yaml
apiVersion: jumpstarter.dev/v1alpha1
kind: ExporterConfig
metadata:
  name: myexporter # The name of the exporter in the cluster.
  namespace: jumpstarter-lab # The namespace of the exporter in the cluster.
tls:
  insecure: false # Enable insecure TLS for testing and development
  ca: "" # A base64-encoded PEM CA certificate bundle to trust
endpoint: "jumpstarter.my-lab.com:1443" # The Jumpstarter service endpoint
token: "******************" # An authentication token
motd: | # Optional message of the day shown to clients when they enter a shell
  Welcome to myexporter!
  Open the console with: j serial console
statusStreamRetryTimeout: 1800 # Retry time after Status fails or ends (default: 1800s)
exitOnLeaseEnd: false # Exit after one lease instead of waiting for the next (default: false)
export: # Configure drivers to expose to the clients
  power:
    type: "jumpstarter_driver_energenie.driver.EnerGenie" # The driver Python class path and type
    config: # Dynamic configuration dict passed to the driver class
      host: "192.168.1.111"
      password: "secret"
      slot: 1
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

If the controller's Status stream fails or ends, and no new update arrives
within `statusStreamRetryTimeout` seconds, `jmp run` exits with status `75`
(`EX_TEMPFAIL`).
The default is 30 minutes. A service manager can then restart the exporter and
reload its configuration. Status `1` remains the intentional shutdown code for
a hook configured with `onFailure: exit`; units using
`RestartPreventExitStatus=1` should still restart on status `75`.
`UNAUTHENTICATED` and `PERMISSION_DENIED` stream errors also exit with status
`75`, so a restart can reload credentials or recover after controller access
to the Kubernetes API is restored.

**Environment Variables**:
- `JUMPSTARTER_GRPC_INSECURE` / `JMP_GRPC_INSECURE` - Set to `1` to disable TLS verification
- `JMP_NAMESPACE` - Namespace in the {term}`controller`, when `metadata` in the config file sets none
- `JMP_NAME` - {term}`Exporter` name, when `metadata` in the config file sets none
- `JMP_DISABLE_COMPRESSION` - Set to `1` to disable stream compression (gzip, xz, bz2, zstd) for driver data transfers

The {term}`exporter` reads its endpoint and token only from the config file. It
renews an internal token before it expires and writes the new value back to the
`token` field of the file; see [Exporters](../../introduction/exporters.md).

**CLI Commands**:
```{code-block}  console
$ jmp config exporter create <alias>  # Create an exporter config
$ jmp config exporter list            # List available exporter configs
$ jmp config exporter edit <alias>    # Open an exporter config in your editor
$ jmp config exporter delete <alias>  # Remove a local exporter config
```

## HTTP Proxy

Clients and exporters reach the network through an HTTP proxy when the standard
proxy environment variables are set:

- `http_proxy` - Proxy for `http://` URLs
- `https_proxy` - Proxy for `https://` URLs and for {term}`gRPC` connections
- `no_proxy` - Comma-separated hostnames, domain suffixes and addresses to reach directly

This covers the {term}`gRPC` connection to the {term}`controller`, `jmp login`,
and the HTTP requests made by Jumpstarter and its drivers, such as an exporter
downloading an image from an `http://` or `https://` URL passed to a flasher.
Set the lowercase names: {term}`gRPC` ignores `HTTP_PROXY`, `HTTPS_PROXY` and
`NO_PROXY`, which the HTTP clients also accept.

```{code-block} console
$ export http_proxy=http://proxy.example.com:3128
$ export https_proxy=http://proxy.example.com:3128
$ export no_proxy=localhost,127.0.0.1,.lab.example.com,192.168.1.20
```

An HTTP request goes through the proxy set for the scheme of its URL, unless its
host is listed in `no_proxy`: with only `https_proxy` set, `http://` URLs are
reached directly. On an exporter, list in `no_proxy` the devices and services of
the local network that drivers reach over HTTP, such as cameras, power switches
or image servers. List each address: some HTTP clients do not understand CIDR
ranges such as `192.168.1.0/24` and would send those requests to the proxy.

For an exporter running as a [`systemd`](https://systemd.io/) service, set the
variables in the unit, for example with `Environment=` lines in the
`[Container]` section of a
[`podman-systemd`](https://docs.podman.io/en/latest/markdown/podman-systemd.unit.5.html)
unit, or with `EnvironmentFile=` in a native service.

## Running Exporters

### Running from the CLI

Exporters can be run manually from the command line for testing:

```{code-block} console
# Run using the exporter config alias (the config file name)
# The exporter keeps serving leases until it is stopped
$ jmp run --exporter my-exporter

# Run using the path to a specific exporter config file
$ jmp run --exporter-config /etc/jumpstarter/exporters/my-exporter.yaml

# Exit after the current lease ends instead of waiting for a new one
$ jmp run --exporter my-exporter --exit-on-lease-end
```

### Running as a Service

For production deployments, it is recommended to use a service manager such as [`systemd`](https://systemd.io/) to keep the {term}`exporter` process alive and restart it when it exits, for example after a {term}`lease` ends with `exitOnLeaseEnd` set or when something goes wrong.

Containerized {term}`exporter`s can be installed as [`systemd`](https://systemd.io/) services using [`podman-systemd`](https://docs.podman.io/en/latest/markdown/podman-systemd.unit.5.html).

Create a `systemd` service file at `/etc/containers/systemd/my-exporter.container` with the following content:

```{code-block} ini
:substitutions:
[Unit]
Description=My exporter

[Container]
ContainerName=my-exporter
# Command to run inside the container
Exec=/jumpstarter/bin/jmp run --exporter my-exporter
# The container image to use
Image=quay.io/jumpstarter-dev/jumpstarter:{{version}}
# Use host networking
Network=host
# Enable privileged mode to allow hardware access
PodmanArgs=--privileged
# Support devices within the container
Volume=/run/udev:/run/udev
Volume=/dev:/dev
# Mount Jumpstarter configs directory
Volume=/etc/jumpstarter:/etc/jumpstarter

[Service]
# Always restart the container after exit
Restart=always
StartLimitBurst=0

[Install]
WantedBy=multi-user.target default.target
```

Then enable and start the service:

```{code-block} console
$ sudo systemctl daemon-reload
$ sudo systemctl enable --now my-exporter
```
