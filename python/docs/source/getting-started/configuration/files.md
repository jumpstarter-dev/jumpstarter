# Files

This page describes configuration files used in Jumpstarter, including their
format, location, related environment variables, and management commands.

Jumpstarter follows a specific hierarchy when loading configurations. See
[Loading Order](loading-order.md) for details on how configurations from
different sources are prioritized.

## User Configuration

**File**: `config.yaml`  
**Location**: `~/.config/jumpstarter/config.yaml`  
**Description**: Defines global user settings including current client
selection.  

**Format**:
```yaml
apiVersion: jumpstarter.dev/v1alpha1
kind: UserConfig
config:
  current-client: default
```

**CLI Commands**: Modified through `jmp config` commands.

## Client Configuration

**File**: Various files with `.yaml` extension  
**Location**: `~/.config/jumpstarter/clients/*.yaml`  
**Description**: Stores client configurations including endpoints, access
tokens, and driver settings.  

**Format**:
```yaml
apiVersion: jumpstarter.dev/v1alpha1
kind: Client
metadata:
  name: myclient
  namespace: jumpstarter-lab
tls:
  insecure: false
  ca: ""
endpoint: "jumpstarter.my-lab.com:1443"
token: "dGhpc2lzYXRva2VuLTEyMzQxMjM0MTIzNEyMzQtc2Rxd3Jxd2VycXdlcnF3ZXJxd2VyLTEyMzQxMjM0MTIz"
drivers:
  allow: ["jumpstarter_drivers_*", "vendorpackage.*"]
```

**Environment Variables**:
- `JUMPSTARTER_GRPC_INSECURE` - Set to `1` to disable TLS verification
- `JMP_CLIENT_CONFIG` - Path to a client configuration file
- `JMP_CLIENT` - Name of a registered client config
- `JMP_NAMESPACE` - Namespace in the controller
- `JMP_NAME` - Client name
- `JMP_ENDPOINT` - gRPC endpoint (overrides config file)
- `JMP_TOKEN` - Auth token (overrides config file)
- `JMP_DRIVERS_ALLOW` - Comma-separated list of allowed driver namespaces
- `JUMPSTARTER_FORCE_SYSTEM_CERTS` - Set to `1` to force system CA certificates

**CLI Commands**:
```shell
jmp config client create <name>  # Create new client config
jmp config client use <name>     # Switch to a different client
jmp config client list           # List available clients
jmp config client delete <name>  # Remove a client config
```

## Exporter Configuration

**File**: Various files with `.yaml` extension  
**Location**: `/etc/jumpstarter/exporters/*.yaml`  
**Description**: Defines exporter settings including connection details and
driver configurations.  

**Format**:
```yaml
apiVersion: jumpstarter.dev/v1alpha1
kind: Exporter
metadata:
  name: myexporter
  namespace: jumpstarter-lab
tls:
  insecure: false
  ca: ""
endpoint: "jumpstarter.my-lab.com:1443"
token: "dGhpc2lzYXRva2VuLTEyMzQxMjM0MTIzNEyMzQtc2Rxd3Jxd2VycXdlcnF3ZXJxd2VyLTEyMzQxMjM0MTIz"
export:
  power:
    type: "jumpstarter_driver_power.driver.PduPower"
    config:
      host: "192.168.1.111"
      port: 1234
      username: "admin"
      password: "secret"
  serial:
    type: "jumpstarter_driver_pyserial.driver.PySerial"
    config:
      url: "/dev/ttyUSB0"
      baudrate: 115200
```

**Environment Variables**:
- `JUMPSTARTER_GRPC_INSECURE` - Set to `1` to disable TLS verification
- `JMP_ENDPOINT` - gRPC endpoint (overrides config file)
- `JMP_TOKEN` - Auth token (overrides config file)
- `JMP_NAMESPACE` - Namespace in the controller
- `JMP_NAME` - Exporter name

**CLI Commands**:
```shell
jmp config exporter create <name>   # Create new exporter config
jmp config exporter list            # List available exporters
jmp config exporter delete <name>   # Remove an exporter config
```

## Running Exporters

Exporters can be run manually or as system services:

```shell
# Run with specific exporter config
jmp run --exporter my-exporter

# Or specify a config path directly
jmp run --exporter-config /etc/jumpstarter/exporters/my-exporter.yaml
```

For persistent operation, exporters can be installed as systemd services using
podman-systemd. Create a systemd service file at
`/etc/containers/systemd/my-exporter.container` with the following content:

```{code-block} ini
:substitutions:
[Unit]
Description=My exporter
[Container]
ContainerName=my-exporter
Exec=/jumpstarter/bin/jmp run --exporter my-exporter
Image=quay.io/jumpstarter-dev/jumpstarter:{{version}}
Network=host
PodmanArgs=--privileged
Volume=/run/udev:/run/udev
Volume=/dev:/dev
Volume=/etc/jumpstarter:/etc/jumpstarter
[Service]
Restart=always
StartLimitBurst=0
[Install]
WantedBy=multi-user.target default.target
```

Then enable and start the service:

```shell
sudo systemctl daemon-reload
sudo systemctl enable --now my-exporter
```