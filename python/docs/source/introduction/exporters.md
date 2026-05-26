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

```{literalinclude} ../examples/introduction/exporter_config.yaml
:language: yaml
```

Note that the `grpcConfig` section supports all options documented in the [gRPC
argument keys
documentation](https://grpc.github.io/grpc/core/group__grpc__arg__keys.html).

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
