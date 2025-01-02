# Getting Started

Jumpstarter provides the following command line tools:

An **admin CLI** tool called `jmpctl`, which is useful for the administration of exporters and
clients in the distributed service. Installation instructions can be found [here](../installation/service-cli.md).

A **client CLI** called `jmp`, you can use it to interact with your connected
hardware, request leases, write tests, and develop custom drivers for your hardware.

A **exporter CLI** called `jmp-exporter`, you can use it to manage local exporter
configurations, and run transient or persistent exporter instances.

The `jmp` and `jmp-exporter` CLI tools are available as part of the `jumpstarter` Python package.
You can learn how to install this package [here](../installation/python-package.md),
but it is also available as a container image
```{code-block}
:substitutions:
quay.io/jumpstarter-dev/jumpstarter:{{version}}
```

To check if you have the client CLI installed, run:

```bash
$ jmp version
```
