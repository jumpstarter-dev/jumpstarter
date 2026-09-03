# Exporters

Jumpstarter uses a program called an {term}`exporter` to enable remote access to your
hardware. The {term}`exporter` typically runs on a {term}`host` system directly connected to
your hardware. It is called an {term}`exporter` because it "exports" the interfaces
connected to the target device for client access.

## Hosts

Typically, the {term}`host` will be a low-cost test system such as a single board
computer with sufficient interfaces to connect to your hardware. It is also
possible to use a local high-power server (or CI runner) as the {term}`host` device.

A {term}`host` can run multiple Exporter instances simultaneously if it needs to
interact with several different devices at the same time.

## Exporter Configuration

Exporters use a YAML configuration file (exporter config) to define which Drivers must be loaded
and the configuration required.

Here is an example exporter config file which would typically be saved at
`/etc/jumpstarter/exporters/demo.yaml`:

```yaml
apiVersion: jumpstarter.dev/v1alpha1
kind: ExporterConfig
metadata:
  namespace: default
  name: demo
endpoint: grpc.jumpstarter.example.com:443
token: xxxxx
grpcConfig:
    grpc.keepalive_time_ms: 20000
export:
  power:
    type: jumpstarter_driver_yepkit.driver.Ykush
    config:
      serial: "YK25838"
      port: "1"
  serial:
    type: "jumpstarter_driver_pyserial.driver.PySerial"
    config:
      url: "/dev/ttyUSB0"
      baudrate: 115200
  storage:
    type: "jumpstarter_driver_sdwire.driver.SDWire"
    config:
      serial: "sdw-00001"
      storage_device: "/dev/disk/by-path/..."
  custom:
    type: "vendorpackage.CustomDriver"
    config:
      hello: "world"
  reference:
    ref: "power"
```

Note that the `grpcConfig` section supports all options documented in the [gRPC
argument keys
documentation](https://grpc.github.io/grpc/core/group__grpc__arg__keys.html).

### Token renewal

Exporters automatically renew internal controller credentials before they expire.
The default year-long token is renewed with 31 days remaining. Shorter tokens are
renewed with 20% of their lifetime remaining, with a minimum lead of 60 seconds
and a cap of half their lifetime. The controller warns when renewal has been
missed: in the final 10% of the lifetime, capped at 30 days. External OIDC
credentials must be renewed through their identity provider; the controller
does not exchange them for internal exporter tokens.

After rotation, the exporter updates its in-memory credential and replaces only
the token value in the current YAML configuration. Other configuration values
and symlinks are preserved; comments and custom formatting are removed. The file
is rewritten readable only by its owner (mode 600), and a warning is logged if it
was readable by other users. Config-management tools should allow the exporter to manage the
token value. The new token survives internal child-process restarts even when
the file cannot be written. In that case, the exporter logs the persistence
failure. If the config is read-only, update its saved credential before the
original token expires. The whole `jmp run` process can exit automatically after
a prolonged controller outage or a stream authentication failure. A service
manager restart then loads the credential from the file, losing the in-memory
replacement.

Rotation never extends a superseded token's original expiry. Only the current
credential in the controller Secret can mint a replacement, and only when at
most half its issued lifetime remains. The immediately previous credential can
recover that same replacement until it expires, without minting another token
or rewriting the Secret. Older credentials are denied rotation, and lineage
denials emit a `RotationDenied` warning event on the Exporter.

Recovery returns a bearer credential: someone who obtains the current token
can use it and later renew it under the same rules. To revoke a compromised
internal exporter credential, delete and recreate the Exporter resource so its
UID changes, then replace the exporter config with the new credential.

## Running an Exporter

To run an Exporter on a {term}`host` system, you must have Python {{requires_python}}
installed and the driver packages specified in the config installed in your
current Python environment.

You can run the {term}`exporter` in your local terminal with:

```console
$ jmp run --exporter myexporter
```

{term}`Exporter`s can also be run in a privileged container or as a `systemd` daemon. It
is recommended to run the {term}`exporter` service in the background with auto-restart
capabilities in case something goes wrong and it needs to be restarted.

## Lifecycle Hooks

{term}`Exporter`s support lifecycle {term}`hook`s that execute shell scripts at {term}`lease`
boundaries. A `beforeLease` hook runs after a {term}`lease` is assigned but before
the client can access drivers, and an `afterLease` hook runs after the
{term}`session` ends but before the {term}`lease` is released.

{term}`Hook`s are configured in the `hooks` section of the exporter config file and
use the {term}`j` CLI to interact with exported devices. For full details, see
[Hooks](hooks.md).
