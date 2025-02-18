# Reference

This section provides details on the Jumpstarter CLI.

## jmp

The base jmp command contains a set of subcommands for the different featuers, those
can also be installed and used independently as `jmp-admin`, `jmp-client`, and
`jmp-exporter`, `jmp-driver`.

```bash
jmp [OPTIONS] COMMAND [ARGS]...
```

### commands

```{toctree}
:maxdepth: 1
jmp-admin.md
```

The `jmp-admin` or `jmp admin` CLI allows administration of exporters and clients in a Kubernetes cluster. To use this CLI, you must have a valid `kubeconfig` and access to the cluter/namespace where the Jumpstarter controller resides.

```{toctree}
:maxdepth: 1
jmp-client.md
```

The `jmp-client` or `jmp client` CLI allows interaction with Jumpstarter as a clients.


```{toctree}
:maxdepth: 1
jmp-exporter.md
```
The `jmp-exporter` or `jmp exporter` CLI allows you to run Jumpstarter exporters as services, container, or standalone.

```{toctree}
:maxdepth: 1
jmp-driver.md
```

The `jmp-driver` or `jmp driver` CLI allows you to list and create Jumpstarter drivers.
