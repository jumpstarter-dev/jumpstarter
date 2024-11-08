# Getting Started

Jumpstarter provides an **admin CLI** tool called `jmpctl`, which is useful
for the administration of exporters and clients in the distributed service,
installation instructions can be found [here](../installation/service-cli.md).

and a **client CLI** called `jmp`, you can use it to interact with your connected
hardware, request leases, write tests, and develop custom drivers for your hardware.

and a **exporter CLI** called `jmp-exporter`, you can use it to manage local exporter
configurations, and run transient or persistent exporter instances.

The `jmp` and `jmp-exporter` CLI tools are available as part of the `jumpstarter` Python package.
You can learn how to install this package [here](../installation/python-package.md),
but it is also available as a container image
```{code-block}
:substitutions:
quay.io/jumpstarter/jumpstarter:{{version}}
```

To check if you have the client CLI installed, run:

```bash
$ jmp version
```
